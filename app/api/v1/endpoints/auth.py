"""Auth endpoints: register-org, login, refresh, set/forgot/reset password, me."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_member
from app.core.rate_limit import limiter
from app.models import User
from app.schemas.auth import (
    ChangePasswordRequest,
    CreateOrgRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    RegisterOrgRequest,
    ResetPasswordRequest,
    SessionResponse,
    SetPasswordRequest,
    SwitchOrgRequest,
    UpdateProfileRequest,
    VerifyTokenRequest,
)
from app.schemas.common import MessageResponse
from app.services import analytics, email_templates
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
    AuthService(db).set_password(member.user, body.password, name=body.name)
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
        msg = email_templates.password_reset(url=url, by_admin=False)
        send_email(to=body.email, subject=msg.subject, body=msg.text, html=msg.html,
                   sender=settings.RESEND_FROM_LOGIN)
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
def me(member=Depends(get_current_member), db: Session = Depends(get_db)) -> MeResponse:
    return MeResponse(
        org_role=member.org_role, must_set_password=member.user.must_set_password,
        user=member.user, org=member.org,
        orgs=AuthService(db).list_user_orgs(member.user_id),
    )


@router.post("/switch-org", response_model=SessionResponse)
@limiter.limit("30/minute")
def switch_org(
    request: Request,
    body: SwitchOrgRequest,
    member=Depends(get_current_member),
    db: Session = Depends(get_db),
) -> SessionResponse:
    """Switch the active org: returns a new session scoped to another org the
    signed-in user belongs to. The frontend swaps tokens and reloads."""
    return SessionResponse(**AuthService(db).switch_org(member.user, body.org_id))


@router.post("/orgs", response_model=SessionResponse)
@limiter.limit("10/minute")
def create_org(
    request: Request,
    body: CreateOrgRequest,
    member=Depends(get_current_member),
    db: Session = Depends(get_db),
) -> SessionResponse:
    """Create a new organization owned by the signed-in user and switch into it."""
    return SessionResponse(
        **AuthService(db).create_org(member.user, org_name=body.org_name, tz=body.timezone)
    )


@router.patch("/me", response_model=MeResponse)
def update_me(
    body: UpdateProfileRequest,
    member=Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MeResponse:
    AuthService(db).update_profile(member.user, name=body.name)
    return MeResponse(
        org_role=member.org_role, must_set_password=member.user.must_set_password,
        user=member.user, org=member.org,
    )


@router.post("/change-password", response_model=MessageResponse)
@limiter.limit("10/minute")
def change_password(
    request: Request,
    body: ChangePasswordRequest,
    member=Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MessageResponse:
    AuthService(db).change_password(
        member.user, current_password=body.current_password, new_password=body.new_password,
    )
    analytics.track(db, event=analytics.PASSWORD_SET, org_id=member.org_id, user_id=member.user_id)
    return MessageResponse(message="Password changed.")
