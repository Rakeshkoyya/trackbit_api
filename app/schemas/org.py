"""Org/member schemas (P0 invite slice; P1-BE-08 expands the Members API)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, model_validator


class InviteMemberRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=20)
    role: str = Field(default="member", pattern="^(admin|member)$")
    # invite_link -> return shareable URL the admin sends themselves (plan B4)
    # email_invite -> also "send" it via the email channel (dev stub logs it)
    mode: str = Field(default="invite_link", pattern="^(invite_link|email_invite)$")

    @model_validator(mode="after")
    def _need_a_contact(self) -> "InviteMemberRequest":
        if not self.email and not self.phone:
            raise ValueError("Provide an email or a phone number.")
        if self.mode == "email_invite" and not self.email:
            raise ValueError("Email invite needs an email address.")
        return self


class InvitedMemberResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    role: str
    invite_url: str
    pending: bool = False  # brand-new account that still needs to set a password


class MemberOut(BaseModel):
    user_id: uuid.UUID
    name: str
    email: str | None = None
    username: str | None = None
    phone: str | None = None
    role: str
    status: str
    last_active_at: datetime | None = None
    has_email: bool = False
    has_phone: bool = False
    pending: bool = False  # invited/created but hasn't set their own password yet


class MembersListResponse(BaseModel):
    members: list[MemberOut] = []


class RoleUpdateRequest(BaseModel):
    role: str = Field(pattern="^(admin|member)$")


class RemoveMemberResponse(BaseModel):
    orphaned_tasks: int = 0


class BulkMemberRow(BaseModel):
    # Name is optional: staff set their own on first login. When omitted, the
    # account's display name defaults to the username until then.
    name: str | None = Field(default=None, max_length=120)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="member", pattern="^(admin|member)$")


class BulkMembersRequest(BaseModel):
    members: list[BulkMemberRow] = Field(min_length=1, max_length=100)


class BulkMemberResult(BaseModel):
    name: str
    username: str
    role: str
    ok: bool
    user_id: uuid.UUID | None = None
    password: str | None = None  # echoed back for the copyable summary on success
    error: str | None = None  # code on failure (username_taken / invalid_username / plan_limit)


class BulkMembersResponse(BaseModel):
    results: list[BulkMemberResult]
    created: int


class UsernameAvailabilityResponse(BaseModel):
    username: str  # normalized form (lowercased/trimmed) the server would store
    available: bool
    error: str | None = None  # "username_taken" | "invalid_username" when not available


class AdminResetPasswordRequest(BaseModel):
    # Present => username user: set this as the new temp password (forces change).
    # Absent  => email user: send a reset link.
    password: str | None = Field(default=None, min_length=8, max_length=128)


class AdminResetPasswordResponse(BaseModel):
    mode: str  # "link_sent" | "password_set"
    password: str | None = None  # echoed temp password when mode == password_set


# ---- org settings + usage (S9) ----------------------------------------
class PlanLimitsOut(BaseModel):
    boards: int | None
    members: int | None
    report_days: int
    report_card: bool
    attachments: bool
    critical: bool


class OrgUsageOut(BaseModel):
    boards: int
    members: int


class OrgSettingsOut(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str
    report_card_hour: int
    plan: str
    plan_status: str
    plan_renews_at: datetime | None = None
    limits: PlanLimitsOut
    usage: OrgUsageOut


class OrgSettingsUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = None
    report_card_hour: int | None = Field(default=None, ge=0, le=23)
