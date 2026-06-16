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


class MemberOut(BaseModel):
    user_id: uuid.UUID
    name: str
    email: str | None = None
    phone: str | None = None
    role: str
    status: str
    last_active_at: datetime | None = None
    has_email: bool = False
    has_phone: bool = False


class MembersListResponse(BaseModel):
    members: list[MemberOut] = []


class RoleUpdateRequest(BaseModel):
    role: str = Field(pattern="^(admin|member)$")


class RemoveMemberResponse(BaseModel):
    orphaned_tasks: int = 0


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
