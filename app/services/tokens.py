"""Auth-token issuance & single-use verification (magic links, invites).

Only token hashes are stored. Consumption is atomic (UPDATE ... WHERE used_at
IS NULL) so a token can be redeemed exactly once even under a race.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AuthError
from app.core.security import generate_raw_token, hash_token
from app.models import AuthToken, Membership, Organization, User


def _now() -> datetime:
    return datetime.now(UTC)


class TokenService:
    def __init__(self, db: Session):
        self.db = db

    def _issue(self, *, user_id, purpose: str, ttl_hours: int, org_id=None) -> str:
        raw = generate_raw_token()
        self.db.add(
            AuthToken(
                user_id=user_id,
                org_id=org_id,
                token_hash=hash_token(raw),
                purpose=purpose,
                expires_at=_now() + timedelta(hours=ttl_hours),
            )
        )
        return raw

    def issue_invite(self, *, user_id, org_id) -> str:
        return self._issue(
            user_id=user_id,
            purpose="invite",
            ttl_hours=settings.INVITE_LINK_EXPIRE_HOURS,
            org_id=org_id,
        )

    def link_url(self, raw_token: str) -> str:
        """The shareable URL the frontend resolves at /join/<token>."""
        return f"{settings.FRONTEND_BASE_URL}/join/{raw_token}"

    def issue_password_reset(self, user_id) -> str:
        return self._issue(
            user_id=user_id,
            purpose="password_reset",
            ttl_hours=settings.PASSWORD_RESET_EXPIRE_HOURS,
        )

    def reset_url(self, raw_token: str) -> str:
        """The shareable URL the frontend resolves at /reset/<token>."""
        return f"{settings.FRONTEND_BASE_URL}/reset/{raw_token}"

    def consume_reset_token(self, raw_token: str) -> tuple[User, Organization, Membership]:
        """Validate + atomically consume a password_reset token; resolve session ctx."""
        token_row = self.db.scalar(
            select(AuthToken).where(
                AuthToken.token_hash == hash_token(raw_token),
                AuthToken.purpose == "password_reset",
            )
        )
        if token_row is None:
            raise AuthError("This link is invalid.", code="bad_link")
        if token_row.expires_at <= _now():
            raise AuthError("This link has expired. Ask for a new one.", code="expired_link")
        consumed = self.db.execute(
            update(AuthToken)
            .where(AuthToken.id == token_row.id, AuthToken.used_at.is_(None))
            .values(used_at=_now())
        )
        if consumed.rowcount == 0:
            raise AuthError("This link has already been used.", code="used_link")
        user = self.db.get(User, token_row.user_id)
        membership = self.db.scalar(
            select(Membership).where(
                Membership.user_id == token_row.user_id, Membership.status == "active"
            )
        )
        if user is None or membership is None:
            raise AuthError("This account is no longer active.", code="revoked")
        org = self.db.get(Organization, membership.org_id)
        return user, org, membership

    def verify_and_consume(
        self, raw_token: str
    ) -> tuple[User, Organization, Membership, str]:
        """Validate, atomically consume, and resolve session context for a token.

        Returns (user, org, membership, purpose) for an invite token. Raises
        AuthError on any invalid/expired/already-used token (mapped to 401).
        """
        token_row = self.db.scalar(
            select(AuthToken).where(
                AuthToken.token_hash == hash_token(raw_token),
                AuthToken.purpose == "invite",
            )
        )
        if token_row is None:
            raise AuthError("This link is invalid.", code="bad_link")
        if token_row.expires_at <= _now():
            raise AuthError("This link has expired. Ask for a new one.", code="expired_link")

        # Atomic single-use: only the first redemption flips used_at.
        consumed = self.db.execute(
            update(AuthToken)
            .where(AuthToken.id == token_row.id, AuthToken.used_at.is_(None))
            .values(used_at=_now())
        )
        if consumed.rowcount == 0:
            raise AuthError("This link has already been used.", code="used_link")

        user = self.db.get(User, token_row.user_id)
        membership = self.db.scalar(
            select(Membership).where(
                Membership.user_id == token_row.user_id,
                Membership.status == "active",
                # invite tokens pin the org; magic links use the user's (single) org
                *( (Membership.org_id == token_row.org_id,) if token_row.org_id else () ),
            )
        )
        if user is None or membership is None:
            raise AuthError("This account is no longer active.", code="revoked")
        org = self.db.get(Organization, membership.org_id)
        return user, org, membership, token_row.purpose
