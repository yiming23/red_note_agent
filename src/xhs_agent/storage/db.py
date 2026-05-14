"""Database engine and session management.

DESIGN.md § 14 invariant: "数据库只通过 repositories 访问，业务层不直接写 SQL 或 ORM".

Use this module for engine/session lifecycle only. For querying, see repositories.py.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from xhs_agent.config import settings


class Base(DeclarativeBase):
    """Base class for all ORM models. Imported by models.py."""

    pass


def _build_engine() -> Engine:
    """Build a SQLAlchemy engine appropriate for the current DATABASE_URL.

    Local: SQLite with thread-safe connection settings.
    Server: PostgreSQL with pool defaults.
    """
    url = settings.database_url
    if url.startswith("sqlite"):
        # SQLite needs check_same_thread=False for use across threads (e.g., Telegram bot worker)
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            echo=False,
        )
    return create_engine(url, pool_pre_ping=True, echo=False)


engine: Engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager that yields a session and handles commit/rollback.

    Usage:
        with session_scope() as s:
            s.add(some_obj)
            # auto-commit on exit; auto-rollback on exception
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
