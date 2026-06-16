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
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class MagicLinkRequest(BaseModel):
    email: EmailStr


class VerifyTokenRequest(BaseModel):
    token: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    email: str | None = None
    phone: str | None = None


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    timezone: str
    plan: str


class SessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    org_role: str
    user: UserOut
    org: OrgOut


class MeResponse(BaseModel):
    org_role: str
    user: UserOut
    org: OrgOut
