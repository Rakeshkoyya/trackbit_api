"""Org settings + usage (S9). Billing lives in services/billing.py."""

from zoneinfo import ZoneInfo, available_timezones

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context import CurrentMember
from app.core.exceptions import ValidationError
from app.core.plans import limits_for
from app.models import Board, Membership
from app.schemas.org import OrgSettingsOut, OrgSettingsUpdate, OrgUsageOut, PlanLimitsOut


class OrgService:
    def __init__(self, db: Session):
        self.db = db

    def _usage(self, org_id) -> OrgUsageOut:
        boards = self.db.scalar(
            select(func.count()).select_from(Board).where(
                Board.org_id == org_id, Board.archived_at.is_(None)
            )
        )
        members = self.db.scalar(
            select(func.count()).select_from(Membership).where(
                Membership.org_id == org_id, Membership.status == "active"
            )
        )
        return OrgUsageOut(boards=boards or 0, members=members or 0)

    def settings(self, member: CurrentMember) -> OrgSettingsOut:
        org = member.org
        lim = limits_for(org.plan)
        return OrgSettingsOut(
            id=org.id,
            name=org.name,
            timezone=org.timezone,
            report_card_hour=org.report_card_hour,
            plan=org.plan,
            plan_status=org.plan_status,
            plan_renews_at=org.plan_renews_at,
            limits=PlanLimitsOut(
                boards=lim.boards, members=lim.members, report_days=lim.report_days,
                report_card=lim.report_card, attachments=lim.attachments, critical=lim.critical,
            ),
            usage=self._usage(org.id),
        )

    def update(self, admin: CurrentMember, req: OrgSettingsUpdate) -> OrgSettingsOut:
        org = admin.org
        if req.name is not None:
            org.name = req.name
        if req.timezone is not None:
            if req.timezone not in available_timezones():
                raise ValidationError("Unknown timezone.", code="bad_timezone")
            ZoneInfo(req.timezone)  # belt-and-suspenders
            org.timezone = req.timezone
        if req.report_card_hour is not None:
            org.report_card_hour = req.report_card_hour
        self.db.flush()
        return self.settings(admin)
