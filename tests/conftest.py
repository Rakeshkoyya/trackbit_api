"""Shared pytest fixtures."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.rate_limit import limiter
from app.main import app
from app.models import Organization, User

# Privileged session for setup/teardown (bypasses RLS) — never used by app code.
_admin_engine = create_engine(settings.migration_database_url, pool_pre_ping=True)
AdminSession = sessionmaker(bind=_admin_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _disable_rate_limiting():
    """Keep functional tests deterministic; the 429 path is tested explicitly."""
    prev = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = prev


@pytest.fixture(autouse=True)
def _no_real_email():
    """Force the console-stub email adapter so tests never hit Resend."""
    prev = settings.RESEND_API_KEY
    settings.RESEND_API_KEY = ""
    yield
    settings.RESEND_API_KEY = prev


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def unique_email() -> str:
    return f"test-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture
def cleanup():
    """Track org/user ids created during a test and hard-delete them afterwards."""
    org_ids: list[uuid.UUID] = []
    user_ids: list[uuid.UUID] = []
    yield {"orgs": org_ids, "users": user_ids}

    db = AdminSession()
    try:
        # Orgs first (cascades boards/tasks/memberships), then global users.
        for oid in org_ids:
            db.execute(delete(Organization).where(Organization.id == oid))
        for uid in user_ids:
            db.execute(delete(User).where(User.id == uid))
        db.commit()
    finally:
        db.close()


@pytest.fixture
def db_session():
    """A direct app-role session for service-level unit tests. These pass explicit
    org/user ids; the `cleanup` fixture hard-deletes them afterwards."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        yield db
        db.commit()
    finally:
        db.close()
