from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update

from novel_anime.core.errors import AppError
from novel_anime.domain.models import InputDocument, ModelProfile, Shot, VisualProfile
from novel_anime.persistence.database import (
    ChapterRow,
    ConfigRow,
    ResultVersionRow,
    RunRow,
    VisualRow,
    WorkRow,
    now,
    sessions,
)


def uid() -> str:
    return uuid4().hex


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def as_dict(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class StudioService:
    def __init__(self) -> None:
        self.sessions = sessions()

    def list_works(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [as_dict(row) for row in session.scalars(select(WorkRow))]

    def create_work(self, title: str) -> str:
        if not title.strip():
            raise AppError("invalid_input", "请填写作品名称。")
        work_id = uid()
        with self.sessions.begin() as session:
            session.add(WorkRow(id=work_id, title=title.strip()))
        return work_id

    def list_chapters(self, work_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [
                as_dict(row)
                for row in session.scalars(select(ChapterRow).where(ChapterRow.work_id == work_id))
            ]

    def save_chapter(
        self,
        work_id: str,
        title: str,
        text: str,
        context: str,
        chapter_id: str | None = None,
        expected_revision: int | None = None,
    ) -> str:
        if not title.strip() or not text.strip() or len(text.encode()) > 2_000_000:
            raise AppError("invalid_input", "请填写标题及正文，正文不得超过 2 MB。")
        with self.sessions.begin() as session:
            if chapter_id:
                row = session.get(ChapterRow, chapter_id)
                if not row or row.work_id != work_id or row.revision != expected_revision:
                    raise AppError("version_conflict", "章节已更新，请刷新后再保存。")
                row.title, row.text, row.context = title, text, context
                row.revision += 1
            else:
                chapter_id = uid()
                session.add(
                    ChapterRow(
                        id=chapter_id,
                        work_id=work_id,
                        title=title,
                        text=text,
                        context=context,
                        revision=1,
                    )
                )
        return chapter_id

    def get_chapter(self, chapter_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            row = session.get(ChapterRow, chapter_id)
            if row is None:
                raise AppError("not_found", "未找到章节。")
            return as_dict(row)

    def list_runs(self, chapter_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [
                as_dict(row)
                for row in session.scalars(
                    select(RunRow)
                    .where(RunRow.chapter_id == chapter_id)
                    .order_by(RunRow.updated_at.desc())
                )
            ]

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise AppError("not_found", "未找到任务。")
            return as_dict(row)

    def create_run(
        self,
        chapter_id: str,
        profile: dict[str, Any],
        style: str,
        aspect_ratio: str,
        preference: str,
        idempotency_key: str,
    ) -> str:
        chapter = self.get_chapter(chapter_id)
        document = InputDocument(
            work_id=chapter["work_id"],
            title=chapter["title"],
            text=chapter["text"],
            context=chapter["context"],
            style=style,
            aspect_ratio=aspect_ratio,
            preference=preference,
            visual_profiles=[
                VisualProfile(**p) for p in self.list_visual_profiles(chapter["work_id"])
            ],
        ).model_dump()
        config = ModelProfile(**profile).model_dump()
        input_hash = digest({"document": document, "profile": config, "template": "1.0"})
        with self.sessions.begin() as session:
            existing = session.scalar(
                select(RunRow).where(RunRow.idempotency_key == idempotency_key)
            )
            if existing:
                if existing.input_hash != input_hash:
                    raise AppError("idempotency_conflict", "提交标识与输入不一致，请重新提交。")
                return str(existing.id)
            run_id = uid()
            session.add(
                RunRow(
                    id=run_id,
                    chapter_id=chapter_id,
                    idempotency_key=idempotency_key,
                    input_hash=input_hash,
                    document=document,
                    profile=config,
                    result={},
                    validation_issues=[],
                )
            )
        return run_id

    def save_run_shots(
        self, run_id: str, shots: list[dict[str, Any]], expected_revision: int
    ) -> None:
        from novel_anime.domain.models import Analysis, Outline, SourceParagraph
        from novel_anime.domain.validation import validate_storyboard

        parsed = [Shot(**shot) for shot in shots]
        with self.sessions.begin() as session:
            row = session.get(RunRow, run_id)
            if not row or row.revision != expected_revision:
                raise AppError("version_conflict", "分镜已更新，请刷新后再保存。")
            if row.status not in ("succeeded", "review_required"):
                raise AppError("invalid_state", "任务尚未完成，请稍后编辑。")
            result = dict(row.result)
            issues = validate_storyboard(
                Analysis(**result["analysis"]),
                Outline(**result["outline"]),
                parsed,
                [SourceParagraph(**p) for p in result["paragraphs"]],
            )
            session.add(
                ResultVersionRow(
                    id=uid(),
                    run_id=run_id,
                    revision=row.revision,
                    result=json.loads(json.dumps(result)),
                )
            )
            result["shots"] = [shot.model_dump() for shot in parsed]
            result["manual_edit"] = True
            row.result = result
            row.validation_issues = issues + ["手工修改后需要复核连续性与剧情表达。"]
            row.status = "review_required"
            row.revision += 1
            row.updated_at = now()

    def load_profiles(self) -> dict[str, Any]:
        return self._load_config("profiles")

    def save_profile(self, profile: dict[str, Any]) -> None:
        config = ModelProfile(**profile).model_dump()
        profiles = self.load_profiles()
        profiles[config["profile_id"]] = config
        self._save_config("profiles", profiles)

    def _load_config(self, key: str) -> dict[str, Any]:
        with self.sessions() as session:
            row = session.get(ConfigRow, key)
            return dict(row.value) if row else {}

    def _save_config(self, key: str, value: dict[str, Any]) -> None:
        with self.sessions.begin() as session:
            session.merge(ConfigRow(key=key, value=value))

    def load_video_config(self) -> dict[str, Any]:
        return self._load_config("video")

    def save_video_config(self, config: dict[str, Any]) -> None:
        allowed = {"base_url", "workflow", "aspect_ratio", "comfy", "ratio", "workflow_note"}
        self._save_config("video", {k: v for k, v in config.items() if k in allowed})

    def list_visual_profiles(self, work_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            rows = session.scalars(
                select(VisualRow).where(VisualRow.work_id == work_id).order_by(VisualRow.version)
            )
            latest = {row.character_id: row.data for row in rows}
            return list(latest.values())

    def save_visual_profile(self, work_id: str, profile: dict[str, Any]) -> None:
        data = VisualProfile(**profile).model_dump()
        with self.sessions.begin() as session:
            previous = session.scalar(
                select(VisualRow)
                .where(VisualRow.work_id == work_id, VisualRow.character_id == data["character_id"])
                .order_by(VisualRow.version.desc())
            )
            expected = previous.version + 1 if previous else 1
            if data["version"] != expected:
                raise AppError("version_conflict", "角色资料版本已更新，请刷新。")
            session.add(
                VisualRow(
                    id=uid(),
                    work_id=work_id,
                    character_id=data["character_id"],
                    version=expected,
                    data=data,
                )
            )

    def pause_run(self, run_id: str) -> None:
        with self.sessions.begin() as session:
            session.execute(
                update(RunRow)
                .where(RunRow.id == run_id, RunRow.status.in_(["pending", "running"]))
                .values(status="pause_requested", updated_at=now())
            )

    def resume_run(self, run_id: str) -> None:
        with self.sessions.begin() as session:
            session.execute(
                update(RunRow)
                .where(
                    RunRow.id == run_id,
                    RunRow.status.in_(["paused", "pause_requested"]),
                    RunRow.owner.is_(None),
                )
                .values(status="pending", updated_at=now())
            )

    def retry_run(self, run_id: str) -> None:
        with self.sessions.begin() as session:
            session.execute(
                update(RunRow)
                .where(
                    RunRow.id == run_id,
                    RunRow.status.in_(["failed", "unknown"]),
                    RunRow.owner.is_(None),
                )
                .values(status="pending", error_message=None, updated_at=now())
            )
