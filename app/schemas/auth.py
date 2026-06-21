"""Auth request/response schemas."""

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterOrgRequest(BaseModel):
    org_name: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    timezone: str = "Asia/Kolkata"


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=320)  # email or username
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class SetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)
    # Optional: first-login name capture (bulk/username staff start with a
    # placeholder name = their username and can set a real one here).
    name: str | None = Field(default=None, max_length=120)


class UpdateProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


class VerifyTokenRequest(BaseModel):
    token: str


class SwitchOrgRequest(BaseModel):
    org_id: uuid.UUID


class CreateOrgRequest(BaseModel):
    org_name: str = Field(min_length=1, max_length=120)
    timezone: str = "Asia/Kolkata"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    email: str | None = None
    username: str | None = None
    phone: str | None = None


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    timezone: str
    plan: str


class OrgMembershipOut(BaseModel):
    """One org the signed-in user is an active member of — drives the switcher."""
    id: uuid.UUID
    name: str
    plan: str
    org_role: str


class SessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    org_role: str
    must_set_password: bool = False
    user: UserOut
    org: OrgOut
    # Every org this user can switch into (includes the current one).
    orgs: list[OrgMembershipOut] = []


class MeResponse(BaseModel):
    org_role: str
    must_set_password: bool = False
    user: UserOut
    org: OrgOut
    orgs: list[OrgMembershipOut] = []
