"""Member invitation (P0 slice).

The full Members API (list, role change, removal + F9 orphaning) lands in
P1-BE-08; this provides the create/invite path so the invite-link → session
loop is end-to-end testable now.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import plans
from app.core.context import CurrentMember
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models import Board, BoardMember, Membership, TaskInstance, User
from app.schemas.org import MemberOut, MembersListResponse
from app.services import analytics, events
from app.services.delivery import send_email
from app.services.tokens import TokenService


class MemberService:
    def __init__(self, db: Session):
        self.db = db

    def list_members(self, member: CurrentMember) -> MembersListResponse:
        rows = self.db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == member.org_id, Membership.status == "active")
            .order_by(User.name)
        ).all()
        return MembersListResponse(
            members=[
                MemberOut(
                    user_id=u.id, name=u.name, email=u.email, phone=u.phone,
                    role=m.org_role, status=m.status, last_active_at=m.last_active_at,
                    has_email=u.email is not None, has_phone=u.phone is not None,
                )
                for m, u in rows
            ]
        )

    def _get_membership(self, org_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
        m = self.db.scalar(
            select(Membership).where(
                Membership.org_id == org_id, Membership.user_id == user_id
            )
        )
        if m is None:
            raise NotFoundError("Member")
        return m

    def _active_admin_count(self, org_id: uuid.UUID) -> int:
        return len(
            list(
                self.db.scalars(
                    select(Membership.id).where(
                        Membership.org_id == org_id, Membership.status == "active",
                        Membership.org_role == "admin",
                    )
                )
            )
        )

    def change_role(self, admin: CurrentMember, user_id: uuid.UUID, role: str) -> MemberOut:
        m = self._get_membership(admin.org_id, user_id)
        if m.status != "active":
            raise ValidationError("That member isn't active.")
        if m.org_role == "admin" and role == "member" and self._active_admin_count(admin.org_id) <= 1:
            raise ValidationError("An organization needs at least one admin.", code="last_admin")
        if m.org_role != role:
            m.org_role = role
            m.token_version += 1  # force re-auth so the new role takes effect (G11)
            self.db.flush()
        user = self.db.get(User, user_id)
        return MemberOut(
            user_id=user.id, name=user.name, email=user.email, phone=user.phone,
            role=m.org_role, status=m.status, last_active_at=m.last_active_at,
            has_email=user.email is not None, has_phone=user.phone is not None,
        )

    def _oldest_admin(self, org_id: uuid.UUID, exclude: uuid.UUID) -> uuid.UUID | None:
        return self.db.scalar(
            select(Membership.user_id)
            .where(
                Membership.org_id == org_id, Membership.status == "active",
                Membership.org_role == "admin", Membership.user_id != exclude,
            )
            .order_by(Membership.created_at.asc())
            .limit(1)
        )

    def _transfer_owned_boards(self, org_id: uuid.UUID, *, departing: uuid.UUID) -> None:
        owned = list(
            self.db.scalars(
                select(Board).where(Board.org_id == org_id, Board.owner_id == departing)
            )
        )
        if not owned:
            return
        new_owner = self._oldest_admin(org_id, exclude=departing)
        if new_owner is None:
            return  # no eligible admin (shouldn't happen — caller guards last-admin)
        for b in owned:
            b.owner_id = new_owner
            # Make sure the new owner can actually see/manage a private board.
            already = self.db.scalar(
                select(BoardMember.id).where(
                    BoardMember.board_id == b.id, BoardMember.user_id == new_owner
                )
            )
            if not already:
                self.db.add(BoardMember(board_id=b.id, user_id=new_owner))
        self.db.flush()

    def remove(self, admin: CurrentMember, user_id: uuid.UUID) -> int:
        """Remove a member; orphan their open tasks (F9). Returns orphaned count."""
        m = self._get_membership(admin.org_id, user_id)
        if m.status != "active":
            return 0
        if m.org_role == "admin" and self._active_admin_count(admin.org_id) <= 1:
            raise ValidationError("You can't remove the last admin.", code="last_admin")

        orphaned = list(
            self.db.scalars(
                select(TaskInstance).where(
                    TaskInstance.org_id == admin.org_id,
                    TaskInstance.assignee_id == user_id,
                    TaskInstance.status == "open",
                )
            )
        )
        for inst in orphaned:
            inst.assignee_id = None
            events.append_event(
                self.db, org_id=admin.org_id, instance_id=inst.id, event_type="edited",
                actor_id=admin.user_id,
                payload={"assignee_id": [str(user_id), None], "reason": "member_removed"},
            )

        # F9: boards this person owned transfer to the oldest remaining admin
        # (reassignable later). Ownership must stay actionable, so the new owner
        # joins any private board they take over.
        self._transfer_owned_boards(admin.org_id, departing=user_id)

        # Drop their private-board memberships.
        self.db.execute(
            BoardMember.__table__.delete().where(BoardMember.user_id == user_id)
        )
        # Revoke the membership and all its sessions (status + token_version).
        m.status = "removed"
        m.token_version += 1
        self.db.flush()
        analytics.track(self.db, event="member_removed", org_id=admin.org_id,
                        user_id=user_id, props={"orphaned": len(orphaned)})
        return len(orphaned)

    def invite(self, admin: CurrentMember, *, name: str, email: str | None,
               phone: str | None, role: str, mode: str) -> dict:
        org_id = admin.org_id  # org from auth context, never from the request

        # Resolve or create the (global) user by contact.
        user = None
        if email:
            user = self.db.scalar(select(User).where(User.email == email))
        if user is None and phone:
            user = self.db.scalar(select(User).where(User.phone == phone))
        if user is None:
            user = User(name=name, email=email, phone=phone)  # passwordless staffer
            self.db.add(user)
            self.db.flush()

        # Membership is created active — open model, instant (PRD D2): the invite
        # link is a login, not an accept/reject gate.
        existing = self.db.scalar(
            select(Membership).where(
                Membership.org_id == org_id, Membership.user_id == user.id
            )
        )
        if existing and existing.status == "active":
            raise ConflictError("This person is already a member.", code="already_member")
        # Free-seat cap applies to new + reactivated members (the core loop isn't
        # paywalled, but team size is).
        plans.enforce_member_quota(self.db, admin.org)
        if existing:
            existing.status = "active"
            existing.org_role = role
        else:
            self.db.add(
                Membership(org_id=org_id, user_id=user.id, org_role=role, status="active")
            )
        self.db.flush()

        raw = TokenService(self.db).issue_invite(user_id=user.id, org_id=org_id)
        url = TokenService(self.db).link_url(raw)

        if mode == "email_invite" and email:
            send_email(
                to=email,
                subject=f"{admin.user.name} added you to {admin.org.name} on TrackBit",
                body=f"Tap to open and get started:\n{url}",
            )

        analytics.track(
            self.db, event=analytics.MEMBER_INVITED, org_id=org_id, user_id=user.id,
            props={"role": role, "mode": mode, "invited_by": str(admin.user_id)},
        )
        return {"user_id": user.id, "name": user.name, "role": role, "invite_url": url}
