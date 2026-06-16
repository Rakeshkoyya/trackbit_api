"""FastAPI dependencies: DB session, authenticated member, admin guard."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.context import CurrentMember
from app.core.database import get_db
from app.core.exceptions import AuthError, ForbiddenError
from app.core.security import decode_access_token
from app.models import Membership, Organization, User

_bearer = HTTPBearer(auto_error=False)


def _engage_rls(db: Session, org_id: uuid.UUID) -> None:
    """Scope this transaction to the org so the RLS safety net applies."""
    db.execute(
        text("SELECT set_config('app.current_org_id', :oid, true)"),
        {"oid": str(org_id)},
    )


def _touch_last_active(db: Session, membership: Membership) -> None:
    """Throttled heartbeat — write at most once per LAST_ACTIVE_THROTTLE_SECONDS."""
    now = datetime.now(UTC)
    last = membership.last_active_at
    if last is None or (now - last) > timedelta(seconds=settings.LAST_ACTIVE_THROTTLE_SECONDS):
        membership.last_active_at = now


def get_current_member(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> CurrentMember:
    if creds is None or not creds.credentials:
        raise AuthError("Authentication required.", code="missing_token")

    try:
        payload = decode_access_token(creds.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Session expired.", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid authentication token.", code="bad_token") from exc

    try:
        user_id = uuid.UUID(payload["sub"])
        org_id = uuid.UUID(payload["org"])
        token_version = int(payload["tv"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthError("Malformed authentication token.", code="bad_token") from exc

    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.org_id == org_id,
            Membership.status == "active",
        )
    )
    if membership is None:
        raise AuthError("You are no longer a member of this organization.", code="revoked")
    # token_version bumps on removal/role change — invalidates old access tokens (G11).
    if membership.token_version != token_version:
        raise AuthError("Session is no longer valid. Please sign in again.", code="revoked")

    _engage_rls(db, org_id)
    user = db.get(User, user_id)
    org = db.get(Organization, org_id)
    _touch_last_active(db, membership)

    return CurrentMember(user=user, org=org, membership=membership)


def require_admin(member: CurrentMember = Depends(get_current_member)) -> CurrentMember:
    if not member.is_admin:
        raise ForbiddenError("This action requires an admin.", code="admin_only")
    return member
