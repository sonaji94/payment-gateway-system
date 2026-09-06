"""SQLAlchemy declarative base with a UUID primary-key convention.

All domain tables use native PostgreSQL ``UUID`` primary keys generated in the
application layer (``uuid4``), avoiding round-trips to a sequence generator and
enabling id-less pre-authorship of object graphs before flush.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""


class UUIDMixin:
    """Convenience mixin attaching a ``UUID pk`` primary key column."""

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )