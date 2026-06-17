"""Auth service: org registration, login, refresh-token rotation."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
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

        membership = self.db.scalar(
            select(Membership).where(
                Membership.user_id == user.id, Membership.status == "active"
            )
        )
        if membership is None:
            raise AuthError("This account is not active in any organization.", code="no_membership")

        org = self.db.get(Organization, membership.org_id)
        membership.last_active_at = _now()
        return self._build_session(user, org, membership)

    def set_password(self, user: User, new_password: str) -> None:
        """Set a user's password and clear the must-set flag (first login / forced change)."""
        user.password_hash = hash_password(new_password)
        user.must_set_password = False
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
