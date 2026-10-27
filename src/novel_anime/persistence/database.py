from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from novel_anime.core.settings import get_settings


def now() -> str:
    return datetime.now(UTC).isoformat()


class Base(DeclarativeBase):
    pass


class WorkRow(Base):
    __tablename__ = "works"
    id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str]


class ChapterRow(Base):
    __tablename__ = "chapters"
    id: Mapped[str] = mapped_column(primary_key=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id"))
    title: Mapped[str]
    text: Mapped[str]
    context: Mapped[str]
    revision: Mapped[int] = mapped_column(default=1)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(primary_key=True)
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id"))
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    input_hash: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")
    stage: Mapped[str] = mapped_column(default="prepare")
    updated_at: Mapped[str] = mapped_column(default=now)
    lease_until: Mapped[str | None]
    owner: Mapped[str | None]
    error_message: Mapped[str | None]
    revision: Mapped[int] = mapped_column(default=1)
    document: Mapped[dict[str, Any]] = mapped_column(JSON)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation_issues: Mapped[list[str]] = mapped_column(JSON, default=list)


class ConfigRow(Base):
    __tablename__ = "configs"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)


class VisualRow(Base):
    __tablename__ = "visual_profiles"
    __table_args__ = (UniqueConstraint("work_id", "character_id", "version"),)
    id: Mapped[str] = mapped_column(primary_key=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id"))
    character_id: Mapped[str]
    version: Mapped[int]
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class CharacterImageRow(Base):
    __tablename__ = "character_images"
    __table_args__ = (UniqueConstraint("work_id", "character_id", "asset_id"),)
    id: Mapped[str] = mapped_column(primary_key=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id"))
    character_id: Mapped[str]
    asset_id: Mapped[str] = mapped_column(unique=True)
    costume_version: Mapped[str]
    purpose: Mapped[str]
    filename: Mapped[str]
    mime_type: Mapped[str]
    size_bytes: Mapped[int]
    width: Mapped[int]
    height: Mapped[int]
    sha256: Mapped[str]
    profile_version: Mapped[int]
    created_at: Mapped[str]


class ResultVersionRow(Base):
    __tablename__ = "result_versions"
    __table_args__ = (UniqueConstraint("run_id", "revision"),)
    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    revision: Mapped[int]
    result: Mapped[dict[str, Any]] = mapped_column(JSON)


def make_engine(url: str | None = None) -> Any:
    engine = create_engine(url or get_settings().database_url, connect_args={"timeout": 15})

    @event.listens_for(engine, "connect")
    def configure(connection: Any, _: Any) -> None:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=15000")

    return engine


def sessions() -> Any:
    return sessionmaker(make_engine(), expire_on_commit=False)
