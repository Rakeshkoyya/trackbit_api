"""Auth service: org registration, login, refresh-token rotation."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import nulls_last, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AuthError, ConflictError
from app.core.security import (
    create_access_token,
    generate_raw_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models import AuthToken, Board, BoardMember, Membership, Organization, User
from app.services import analytics


def _now() -> datetime:
    return datetime.now(UTC)


class AuthService:
    def __init__(self, db: Session):
        self.db = db

    # ---- token helpers -------------------------------------------------
    def _issue_refresh_token(self, user_id: uuid.UUID, org_id: uuid.UUID) -> str:
        raw = generate_raw_token()
        self.db.add(
            AuthToken(
                user_id=user_id,
                org_id=org_id,
                token_hash=hash_token(raw),
                purpose="refresh",
                expires_at=_now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            )
        )
        return raw

    def _set_org_scope(self, value: str) -> None:
        """Set this transaction's RLS org scope. '' lifts scoping for legitimate
        cross-org reads (a user may belong to many orgs); a specific org id scopes
        writes to that org (used when seeding a brand-new org, since the request is
        otherwise still scoped to the user's previous org)."""
        self.db.execute(
            text("SELECT set_config('app.current_org_id', :v, true)"), {"v": value}
        )

    def list_user_orgs(self, user_id: uuid.UUID) -> list[dict]:
        """Every active org this user can switch into (current one included),
        most-recently-active first. Reads across orgs, so it lifts RLS scoping."""
        self._set_org_scope("")
        rows = self.db.execute(
            select(Organization, Membership.org_role)
            .join(Membership, Membership.org_id == Organization.id)
            .where(Membership.user_id == user_id, Membership.status == "active")
            .order_by(nulls_last(Membership.last_active_at.desc()), Membership.created_at.asc())
        ).all()
        return [
            {"id": org.id, "name": org.name, "plan": org.plan, "org_role": role}
            for org, role in rows
        ]

    def build_session(self, user: User, org: Organization, membership: Membership) -> dict:
        """Public: issue an access+refresh session (used by token-verify flows)."""
        return self._build_session(user, org, membership)

    def _build_session(self, user: User, org: Organization, membership: Membership) -> dict:
        access = create_access_token(
            user_id=user.id,
            org_id=org.id,
            org_role=membership.org_role,
            token_version=membership.token_version,
        )
        refresh = self._issue_refresh_token(user.id, org.id)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "bearer",
            "org_role": membership.org_role,
            "must_set_password": user.must_set_password,
            "user": user,
            "org": org,
            "orgs": self.list_user_orgs(user.id),
        }

    # ---- flows ---------------------------------------------------------
    def register_org(
        self, *, org_name: str, name: str, email: str, password: str, tz: str
    ) -> dict:
        """F1: create org + admin user + membership + default 'General' board atomically."""
        existing = self.db.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise ConflictError(
                "An account with this email already exists.", code="email_taken"
            )

        user = User(name=name, email=email, password_hash=hash_password(password))
        self.db.add(user)
        self.db.flush()

        org = Organization(name=org_name, timezone=tz)
        self.db.add(org)
        self.db.flush()

        membership = Membership(
            org_id=org.id, user_id=user.id, org_role="admin", last_active_at=_now()
        )
        self.db.add(membership)

        # Every new org starts with one public board so Home is never bare (F1).
        general = Board(
            org_id=org.id, name="General", visibility="public", category="tasks",
            created_by=user.id, owner_id=user.id,
        )
        self.db.add(general)
        self.db.flush()
        # Owner is always a board member (keeps them viewing if flipped private).
        self.db.add(BoardMember(board_id=general.id, user_id=user.id))
        self.db.flush()
        analytics.track(self.db, event=analytics.ORG_REGISTERED, org_id=org.id, user_id=user.id)
        return self._build_session(user, org, membership)

    def login(self, *, identifier: str, password: str) -> dict:
        ident = (identifier or "").strip()
        if "@" in ident:
            user = self.db.scalar(select(User).where(User.email == ident))
        else:
            user = self.db.scalar(select(User).where(User.username == ident.lower()))
        if user is None or not user.password_hash or not verify_password(password, user.password_hash):
            raise AuthError("Incorrect email/username or password.", code="bad_credentials")

        # A user may now belong to several orgs — land them in the one they used
        # most recently (they can switch from the account menu). Login is unauth'd,
        # so RLS isn't engaged yet and this sees every membership.
        membership = self.db.scalar(
            select(Membership)
            .where(Membership.user_id == user.id, Membership.status == "active")
            .order_by(nulls_last(Membership.last_active_at.desc()), Membership.created_at.asc())
            .limit(1)
        )
        if membership is None:
            raise AuthError("This account is not active in any organization.", code="no_membership")

        org = self.db.get(Organization, membership.org_id)
        membership.last_active_at = _now()
        return self._build_session(user, org, membership)

    def switch_org(self, user: User, target_org_id: uuid.UUID) -> dict:
        """Issue a fresh session scoped to another org the user is a member of.
        The new org is proven here (active membership) and then carried only in the
        signed token — never trusted from a request param thereafter (§7.3)."""
        self._set_org_scope("")  # look across the user's orgs
        membership = self.db.scalar(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.org_id == target_org_id,
                Membership.status == "active",
            )
        )
        if membership is None:
            raise AuthError("You are not a member of that organization.", code="not_member")
        org = self.db.get(Organization, target_org_id)
        membership.last_active_at = _now()
        return self._build_session(user, org, membership)

    def create_org(self, user: User, *, org_name: str, tz: str) -> dict:
        """Create a new org owned by an already-signed-in user, then switch into it.
        Mirrors register_org's tail but reuses the existing user (no new account)."""
        org = Organization(name=org_name, timezone=tz)
        self.db.add(org)
        self.db.flush()
        # The seed rows below belong to the NEW org; point RLS there so the
        # memberships/boards WITH CHECK policy passes (the request is still scoped
        # to the user's previous org).
        self._set_org_scope(str(org.id))
        membership = Membership(
            org_id=org.id, user_id=user.id, org_role="admin", last_active_at=_now()
        )
        self.db.add(membership)
        general = Board(
            org_id=org.id, name="General", visibility="public", category="tasks",
            created_by=user.id, owner_id=user.id,
        )
        self.db.add(general)
        self.db.flush()
        self.db.add(BoardMember(board_id=general.id, user_id=user.id))
        self.db.flush()
        analytics.track(self.db, event=analytics.ORG_REGISTERED, org_id=org.id, user_id=user.id)
        return self._build_session(user, org, membership)

    def update_profile(self, user: User, *, name: str) -> None:
        """Update the signed-in user's display name. Name isn't in the JWT, so no
        re-auth is needed — /me returns the new value immediately."""
        user.name = name.strip()
        self.db.flush()

    def change_password(self, user: User, *, current_password: str, new_password: str) -> None:
        """Self-service password change: verify the current password first."""
        if not user.password_hash or not verify_password(current_password, user.password_hash):
            raise AuthError("Current password is incorrect.", code="bad_password")
        user.password_hash = hash_password(new_password)
        user.must_set_password = False
        self.db.flush()

    def set_password(self, user: User, new_password: str, *, name: str | None = None) -> None:
        """Set a user's password and clear the must-set flag (first login / forced change).

        Optionally set the display name too — bulk/username staff finish onboarding
        by choosing their real name here (they start with a username placeholder).
        """
        user.password_hash = hash_password(new_password)
        user.must_set_password = False
        if name and name.strip():
            user.name = name.strip()
        self.db.flush()

    def refresh(self, *, raw_refresh: str) -> dict:
        """Rotate the refresh token: consume the presented one, issue a fresh pair."""
        token_row = self.db.scalar(
            select(AuthToken).where(
                AuthToken.token_hash == hash_token(raw_refresh),
                AuthToken.purpose == "refresh",
            )
        )
        if token_row is None or token_row.used_at is not None:
            raise AuthError("Invalid or already-used refresh token.", code="bad_refresh")
        if token_row.expires_at <= _now():
            raise AuthError("Refresh token has expired. Please sign in again.", code="expired_refresh")

        membership = self.db.scalar(
            select(Membership).where(
                Membership.user_id == token_row.user_id,
                Membership.org_id == token_row.org_id,
                Membership.status == "active",
            )
        )
        if membership is None:
            raise AuthError("Session is no longer valid.", code="revoked")

        token_row.used_at = _now()  # rotation: old token can never be reused
        user = self.db.get(User, token_row.user_id)
        org = self.db.get(Organization, token_row.org_id)
        return self._build_session(user, org, membership)
