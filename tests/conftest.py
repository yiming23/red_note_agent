"""Shared pytest fixtures.

Provides an isolated in-memory SQLite engine per test so DB-touching tests don't
interfere with each other and don't pollute the local xhs_agent.db.
"""

from __future__ import annotations

import os

# Force in-memory DB before any xhs_agent module is imported in test runs.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("ANTHROPIC_API_KEY", "")  # force dry-run mode in tests

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def in_memory_db():
    """Yields a fresh in-memory engine + sessionmaker; tables created from metadata."""
    from xhs_agent.storage.db import Base
    from xhs_agent.storage import models  # noqa: F401  ensure all models registered

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    yield SessionMaker

    Base.metadata.drop_all(engine)
    engine.dispose()
