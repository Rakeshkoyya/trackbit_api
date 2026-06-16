"""All models — importing this module registers everything on Base.metadata."""

from app.models.analytics import AnalyticsEvent
from app.models.auth_token import AuthToken
from app.models.billing import Invoice
from app.models.board import Board, BoardCategory, BoardMember
from app.models.notification import DeviceToken, Notification
from app.models.org import Membership, Organization
from app.models.task import EVENT_TYPES, Attachment, TaskEvent, TaskInstance, TaskTemplate
from app.models.user import User

__all__ = [
    "AnalyticsEvent",
    "Attachment",
    "AuthToken",
    "Board",
    "BoardCategory",
    "BoardMember",
    "DeviceToken",
    "EVENT_TYPES",
    "Invoice",
    "Membership",
    "Notification",
    "Organization",
    "TaskEvent",
    "TaskInstance",
    "TaskTemplate",
    "User",
]
