"""Free vs Pro plan limits + enforcement helpers (plan P4-BE-01, R6).

One price: Pro is ₹500/month flat for the whole org. The **core loop is never
paywalled** — assign, claim, pass, complete, the daily ritual all work on Free.
Limits gate breadth (boards, seats) and premium surfaces (EOD report card,
attachments, critical alarms), and every breach raises a structured
PlanLimitError the UI turns into an upgrade prompt — never a silent failure.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import PlanLimitError
from app.models import Board, Membership, Organization


@dataclass(frozen=True)
class PlanLimits:
    boards: int | None  # None = unlimited
    members: int | None
    report_days: int  # how far back reports may look
    report_card: bool  # EOD admin report card
    attachments: bool
    critical: bool  # critical/alarm tasks


FREE = PlanLimits(boards=2, members=8, report_days=14, report_card=False, attachments=False, critical=False)
PRO = PlanLimits(boards=None, members=None, report_days=365, report_card=True, attachments=True, critical=True)


def limits_for(plan: str) -> PlanLimits:
    return PRO if plan == "pro" else FREE


def is_pro(org: Organization) -> bool:
    return org.plan == "pro"


# ---- enforcement (called from the relevant POST paths) ----------------
def enforce_board_quota(db: Session, org: Organization) -> None:
    lim = limits_for(org.plan)
    if lim.boards is None:
        return
    count = db.scalar(
        select(func.count()).select_from(Board).where(
            Board.org_id == org.id, Board.archived_at.is_(None)
        )
    )
    if count >= lim.boards:
        raise PlanLimitError(
            "boards",
            limit=lim.boards,
            current=count,
            message=(
                f"Free plan includes {lim.boards} boards. "
                "Upgrade to Pro for unlimited boards."
            ),
        )


def enforce_member_quota(db: Session, org: Organization) -> None:
    lim = limits_for(org.plan)
    if lim.members is None:
        return
    count = db.scalar(
        select(func.count()).select_from(Membership).where(
            Membership.org_id == org.id, Membership.status == "active"
        )
    )
    if count >= lim.members:
        raise PlanLimitError(
            "members",
            limit=lim.members,
            current=count,
            message=(
                f"Free plan includes {lim.members} members. "
                "Upgrade to Pro to add more of your team."
            ),
        )


def enforce_critical_allowed(org: Organization) -> None:
    if not limits_for(org.plan).critical:
        raise PlanLimitError(
            "critical",
            message="Critical alarm-style reminders are a Pro feature. Upgrade to enable them.",
        )


def enforce_attachments_allowed(org: Organization) -> None:
    if not limits_for(org.plan).attachments:
        raise PlanLimitError(
            "attachments",
            message="Notes & photo attachments are a Pro feature. Upgrade to enable them.",
        )
