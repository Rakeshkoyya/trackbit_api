"""Auth endpoints: register-org, login, refresh, set/forgot/reset password, me."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_member
from app.core.rate_limit import limiter
from app.models import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    RegisterOrgRequest,
    ResetPasswordRequest,
    SessionResponse,
    SetPasswordRequest,
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
        org_name=body.org_name, name=body.name, email=body.email,
        password=body.password, tz=body.timezone,
    )
    return SessionResponse(**session)


@router.post("/login", response_model=SessionResponse)
@limiter.limit("10/minute")
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)) -> SessionResponse:
    session = AuthService(db).login(identifier=body.identifier, password=body.password)
    return SessionResponse(**session)


@router.post("/refresh", response_model=SessionResponse)
@limiter.limit("30/minute")
def refresh(request: Request, body: RefreshRequest, db: Session = Depends(get_db)) -> SessionResponse:
    return SessionResponse(**AuthService(db).refresh(raw_refresh=body.refresh_token))


@router.post("/set-password", response_model=MessageResponse)
def set_password(
    body: SetPasswordRequest,
    member=Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MessageResponse:
    AuthService(db).set_password(member.user, body.password)
    analytics.track(db, event=analytics.PASSWORD_SET, org_id=member.org_id, user_id=member.user_id)
    return MessageResponse(message="Password set.")


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit("5/minute")
def forgot_password(
    request: Request, body: ForgotPasswordRequest, db: Session = Depends(get_db)
) -> MessageResponse:
    # Always 200 — never reveal whether an email is registered.
    user = db.scalar(select(User).where(User.email == body.email))
    if user is not None and user.password_hash is not None:
        raw = TokenService(db).issue_password_reset(user.id)
        url = TokenService(db).reset_url(raw)
        send_email(to=body.email, subject="Reset your TrackBit password",
                   body=f"Tap to choose a new password:\n{url}")
        analytics.track(db, event=analytics.PASSWORD_RESET_REQUESTED, user_id=user.id)
    return MessageResponse(message="If that email is registered, a reset link is on its way.")


@router.post("/reset-password", response_model=SessionResponse)
@limiter.limit("20/minute")
def reset_password(
    request: Request, body: ResetPasswordRequest, db: Session = Depends(get_db)
) -> SessionResponse:
    svc = AuthService(db)
    user, org, membership = TokenService(db).consume_reset_token(body.token)
    svc.set_password(user, body.password)
    return SessionResponse(**svc.build_session(user, org, membership))


@router.post("/verify", response_model=SessionResponse)
@limiter.limit("20/minute")
def verify(request: Request, body: VerifyTokenRequest, db: Session = Depends(get_db)) -> SessionResponse:
    user, org, membership, purpose = TokenService(db).verify_and_consume(body.token)
    if purpose == "invite":
        analytics.track(db, event=analytics.MEMBER_JOINED, org_id=org.id, user_id=user.id)
    return SessionResponse(**AuthService(db).build_session(user, org, membership))


@router.get("/me", response_model=MeResponse)
def me(member=Depends(get_current_member)) -> MeResponse:
    return MeResponse(
        org_role=member.org_role, must_set_password=member.user.must_set_password,
        user=member.user, org=member.org,
    )
