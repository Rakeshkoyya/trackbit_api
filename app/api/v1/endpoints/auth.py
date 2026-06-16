"""Auth endpoints: register-org, login, refresh, me."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_member
from app.core.rate_limit import limiter
from app.schemas.auth import (
    LoginRequest,
    MagicLinkRequest,
    MeResponse,
    RefreshRequest,
    RegisterOrgRequest,
    SessionResponse,
    VerifyTokenRequest,
)
from app.schemas.common import MessageResponse
from app.services import analytics
from app.services.auth import AuthService
from app.services.delivery import send_email
from app.services.tokens import TokenService

router = APIRouter()


@router.post("/register-org", response_model=SessionResponse)
@limiter.limit("5/minute")
def register_org(
    request: Request, body: RegisterOrgRequest, db: Session = Depends(get_db)
) -> SessionResponse:
    session = AuthService(db).register_org(
        org_name=body.org_name,
        name=body.name,
        email=body.email,
        password=body.password,
        tz=body.timezone,
    )
    return SessionResponse(**session)


@router.post("/login", response_model=SessionResponse)
@limiter.limit("10/minute")
def login(
    request: Request, body: LoginRequest, db: Session = Depends(get_db)
) -> SessionResponse:
    session = AuthService(db).login(email=body.email, password=body.password)
    return SessionResponse(**session)


@router.post("/refresh", response_model=SessionResponse)
@limiter.limit("30/minute")
def refresh(
    request: Request, body: RefreshRequest, db: Session = Depends(get_db)
) -> SessionResponse:
    session = AuthService(db).refresh(raw_refresh=body.refresh_token)
    return SessionResponse(**session)


@router.post("/magic-link/request", response_model=MessageResponse)
@limiter.limit("5/minute")
def request_magic_link(
    request: Request, body: MagicLinkRequest, db: Session = Depends(get_db)
) -> MessageResponse:
    # Always 200 — never reveal whether an email is registered.
    from sqlalchemy import select

    from app.models import User

    user = db.scalar(select(User).where(User.email == body.email))
    if user is not None:
        raw = TokenService(db).issue_magic_link(user.id)
        url = TokenService(db).link_url(raw)
        send_email(to=body.email, subject="Your TrackBit sign-in link",
                   body=f"Tap to sign in:\n{url}")
        analytics.track(db, event=analytics.MAGIC_LINK_REQUESTED, user_id=user.id)
    return MessageResponse(message="If that email is registered, a sign-in link is on its way.")


@router.post("/verify", response_model=SessionResponse)
@limiter.limit("20/minute")
def verify(
    request: Request, body: VerifyTokenRequest, db: Session = Depends(get_db)
) -> SessionResponse:
    user, org, membership, purpose = TokenService(db).verify_and_consume(body.token)
    if purpose == "invite":
        analytics.track(db, event=analytics.MEMBER_JOINED, org_id=org.id, user_id=user.id)
    session = AuthService(db).build_session(user, org, membership)
    return SessionResponse(**session)


@router.get("/me", response_model=MeResponse)
def me(member=Depends(get_current_member)) -> MeResponse:
    return MeResponse(org_role=member.org_role, user=member.user, org=member.org)
