"""Authenticated request context."""

import uuid
from dataclasses import dataclass

from app.models import Membership, Organization, User


@dataclass
class CurrentMember:
    """Resolved auth context for the current request.

    org_id always comes from here (the verified token), never from request
    params — that is the one architectural law (plan §7.3).
    """

    user: User
    org: Organization
    membership: Membership

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def org_id(self) -> uuid.UUID:
        return self.org.id

    @property
    def org_role(self) -> str:
        return self.membership.org_role

    @property
    def is_admin(self) -> bool:
        return self.membership.org_role == "admin"
