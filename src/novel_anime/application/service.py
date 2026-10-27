from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select, update

from novel_anime.core.errors import AppError
from novel_anime.domain.models import (
    InputDocument,
    ModelProfile,
    ScriptCharacter,
    SourceParagraph,
    StoryScript,
    VisualProfile,
)
from novel_anime.persistence.database import (
    ChapterRow,
    ConfigRow,
    ResultVersionRow,
    RunRow,
    ShotImageRow,
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


STAGES = ("script",)


def completed_stages(result: dict[str, Any]) -> list[str]:
    completed = [stage for stage in STAGES if result.get(stage)]
    if result.get("script_pending"):
        completed = [stage for stage in completed if stage != "script"]
    return completed


def as_dict(row: Any) -> dict[str, Any]:
    value = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    if isinstance(row, RunRow):
        result = dict(value["result"])
        script = result.get("script") or {}
        if not result.get("shots") and script.get("shots"):
            result["shots"] = script["shots"]
        value["result"] = result
    return value


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
            runs = [
                as_dict(row)
                for row in session.scalars(
                    select(RunRow)
                    .where(RunRow.chapter_id == chapter_id)
                    .order_by(RunRow.updated_at.desc())
                )
            ]
        chapter = self.get_chapter(chapter_id)
        latest = runs[0] if runs else None
        for run in runs:
            outdated = any(
                run["document"].get(key, "") != chapter[key] for key in ("text", "context", "title")
            )
            run["completed_stages"] = completed_stages(run["result"])
            run["stale_stages"] = [
                stage
                for stage in STAGES
                if run["result"].get(stage)
                and (
                    outdated
                    or (
                        latest
                        and digest(run["result"][stage]) != digest(latest["result"].get(stage))
                    )
                )
            ]
        return runs

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise AppError("not_found", "未找到任务。")
            return as_dict(row)

    def chapter_overview(self, chapter_id: str) -> dict[str, Any]:
        chapter = self.get_chapter(chapter_id)
        runs = self.list_runs(chapter_id)
        # Only story-script runs carry the shot fields the drawing workbench needs.
        run = next(
            (
                item
                for item in runs
                if item.get("result", {}).get("script") and item["result"].get("shots")
            ),
            None,
        )
        if run is None:
            return {"shots": [], "run": None}
        profiles = {p["character_id"]: p for p in self.list_visual_profiles(chapter["work_id"])}
        assets = self.list_character_images(chapter["work_id"])
        characters = {
            p["character_id"]: p for p in run["result"].get("script", {}).get("characters", [])
        }
        snapshots = {p["character_id"]: p for p in run["document"].get("visual_profiles", [])}
        shots = []
        for shot in sorted(run["result"]["shots"], key=lambda item: item["order"]):
            references: list[dict[str, Any]] = []
            subjects: list[dict[str, Any]] = []
            for character_id in dict.fromkeys(shot.get("character_ids", [])):
                profile = profiles.get(character_id, {})
                character = characters.get(character_id, {})
                snapshot = snapshots.get(character_id, {})
                description = "；".join(
                    part
                    for part in (
                        character.get("appearance") or profile.get("appearance", ""),
                        character.get("costume") or profile.get("costume", ""),
                    )
                    if part
                )
                adopted = set(profile.get("reference_assets", []))
                images = [
                    {"asset_id": asset["asset_id"]}
                    for asset in assets
                    if asset["character_id"] == character_id
                    and asset["asset_id"] in adopted
                    and asset["purpose"] != "candidate"
                ]
                subjects.append(
                    {
                        "character_id": character_id,
                        "name": character.get("name")
                        or snapshot.get("name")
                        or profile.get("name", character_id),
                        "description": description,
                        "images": images,
                    }
                )
                references.extend(
                    {
                        "asset_id": asset["asset_id"],
                        "character_name": profile.get("name", character_id),
                    }
                    for asset in assets
                    if asset["character_id"] == character_id
                    and asset["asset_id"] in adopted
                    and asset["purpose"] != "candidate"
                )
            image_source = self.shot_image_source(run["id"], shot["shot_id"])
            shots.append(
                {
                    **shot,
                    "subjects": subjects,
                    "reference_images": references,
                    "image_source": image_source,
                }
            )
        return {
            "shots": shots,
            "run": {key: run[key] for key in ("id", "status", "updated_at")},
        }

    def video_plan(self, chapter_id: str) -> dict[str, Any]:
        """Compile the newest script into video prompt groups; never re-runs the text stage."""
        from novel_anime.domain.video import VIDEO_MAX_SECONDS, VIDEO_TARGET_SECONDS, compile_plan

        self.get_chapter(chapter_id)
        run = next(
            (
                item
                for item in self.list_runs(chapter_id)
                if item.get("result", {}).get("script") and item["result"].get("shots")
            ),
            None,
        )
        config = self.load_video_config()
        aspect_ratio = str(config.get("aspect_ratio") or "16:9")
        target = float(config.get("target_seconds") or VIDEO_TARGET_SECONDS)
        ceiling = float(config.get("max_seconds") or VIDEO_MAX_SECONDS)
        if run is None:
            return {
                "groups": [],
                "run": None,
                "preset": None,
                "negative": "",
                "target_seconds": target,
                "max_seconds": ceiling,
                "aspect_ratio": aspect_ratio,
            }
        result = run["result"]
        plan = compile_plan(
            result.get("shots", []),
            result.get("script", {}).get("characters", []),
            self.video_prompt(),
            {"aspect_ratio": aspect_ratio, "target_seconds": target, "max_seconds": ceiling},
        )
        shots = {str(shot["shot_id"]): shot for shot in result.get("shots", [])}
        for group in plan["groups"]:
            for item in group["passes"]:
                first = shots.get(item["shot_ids"][0], {}) if item["shot_ids"] else {}
                item["first_shot"] = {
                    "shot_id": first.get("shot_id", ""),
                    "frame": first.get("frame", ""),
                    "shot_size": first.get("shot_size", ""),
                }
        return {
            **plan,
            "run": {key: run[key] for key in ("id", "status", "updated_at", "revision")},
            "target_seconds": target,
            "max_seconds": ceiling,
            "aspect_ratio": aspect_ratio,
        }

    def shot_image_source(self, run_id: str, shot_id: str) -> dict[str, Any]:
        """Image prompts read only script rows, so drawing never re-runs the text stage."""
        run = self.get_run(run_id)
        image_run_id = run["document"].get("shot_image_run_id", run_id)
        result = run["result"]
        shot = next((s for s in result.get("shots", []) if s["shot_id"] == shot_id), None)
        if shot is None:
            raise AppError("not_found", "分镜不存在，请重新选择。")
        character_ids = list(dict.fromkeys(shot.get("character_ids", [])))
        people = {
            p["character_id"]: p
            for p in result.get("script", {}).get("characters", [])
            if p.get("character_id") in set(character_ids)
        }
        profiles = {
            p["character_id"]: p
            for p in run["document"].get("visual_profiles", [])
            if p["character_id"] in set(character_ids)
        }
        subjects = []
        for cid in character_ids:
            person = people.get(cid, {})
            profile = profiles.get(cid, {})
            prompt = "；".join(
                part
                for part in (
                    person.get("appearance") or profile.get("appearance", ""),
                    person.get("costume") or profile.get("costume", ""),
                )
                if part
            )
            subjects.append(
                {
                    "character_id": cid,
                    "name": person.get("name") or profile.get("name", cid),
                    "prompt": prompt,
                }
            )
        character_text = "\n".join(
            f"{item['name']}：{item['prompt']}" for item in subjects if item["prompt"]
        ) or ("无人物" if not character_ids else "本版脚本未记录人物外观，请重新生成脚本。")
        environment = shot.get("setting") or "本版脚本未记录场景，请先补充场景描述。"
        content = {
            "character_prompt": character_text,
            "environment_prompt": environment,
            "shot_prompt": "\n".join(
                str(shot.get(key, ""))
                for key in ("shot_size", "camera", "frame", "action")
                if shot.get(key)
            ),
        }
        source_hash = digest({"shot": shot, "content": content})
        with self.sessions() as session:
            images = [
                as_dict(row)
                for row in session.scalars(
                    select(ShotImageRow)
                    .where(
                        ShotImageRow.run_id == image_run_id,
                        ShotImageRow.shot_id == shot_id,
                        ShotImageRow.source_hash == source_hash,
                    )
                    .order_by(ShotImageRow.created_at.desc())
                )
            ]
        return {
            **content,
            "subjects": subjects,
            "source_hash": source_hash,
            "images": images,
        }

    def save_shot_images(
        self, run_id: str, shot_id: str, source_hash: str, images: list[dict[str, Any]]
    ) -> None:
        if self.shot_image_source(run_id, shot_id)["source_hash"] != source_hash:
            raise AppError("version_conflict", "分镜已修改，请刷新后重新生成。")
        image_run_id = self.get_run(run_id)["document"].get("shot_image_run_id", run_id)
        with self.sessions.begin() as session:
            session.add_all(
                [
                    ShotImageRow(
                        run_id=image_run_id,
                        shot_id=shot_id,
                        source_hash=source_hash,
                        selected=False,
                        **image,
                    )
                    for image in images
                ]
            )

    def select_shot_image(self, run_id: str, shot_id: str, asset_id: str) -> None:
        source = self.shot_image_source(run_id, shot_id)
        if not any(image["asset_id"] == asset_id for image in source["images"]):
            raise AppError("version_conflict", "图片不属于当前分镜版本，请刷新核对。")
        image_run_id = self.get_run(run_id)["document"].get("shot_image_run_id", run_id)
        image = next(item for item in source["images"] if item["asset_id"] == asset_id)
        with self.sessions.begin() as session:
            session.execute(
                update(ShotImageRow)
                .where(
                    ShotImageRow.run_id == image_run_id,
                    ShotImageRow.shot_id == shot_id,
                    ShotImageRow.source_hash == source["source_hash"],
                    ShotImageRow.kind == image["kind"],
                    ShotImageRow.subject_id == image["subject_id"],
                )
                .values(selected=False)
            )
            session.execute(
                update(ShotImageRow).where(ShotImageRow.asset_id == asset_id).values(selected=True)
            )

    def create_run(
        self,
        chapter_id: str,
        profile: dict[str, Any],
        style: str,
        aspect_ratio: str,
        preference: str,
        idempotency_key: str,
        target_stage: str | None = None,
        source_run_id: str | None = None,
        source_revision: int | None = None,
        selected_stages: list[str] | None = None,
    ) -> str:
        if target_stage not in (None, "script") or selected_stages not in (None, ["script"]):
            raise AppError("invalid_input", "故事脚本只有一步，请直接生成脚本。")
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
        from novel_anime.core.settings import ROOT

        saved_templates = self._load_config("prompt_templates")
        document["prompt_templates"] = {
            "script": saved_templates.get("script")
            or (ROOT / "prompts" / "script.txt").read_text(encoding="utf-8")
        }
        initial: dict[str, Any] = {}
        document["target_stage"] = "script"
        document["selected_stages"] = ["script"]
        document["chapter_revision"] = chapter["revision"]
        document["source_run_id"] = source_run_id
        document["source_revision"] = source_revision
        if source_run_id is not None:
            document["shot_image_run_id"] = self.get_run(source_run_id)["document"].get(
                "shot_image_run_id", source_run_id
            )
        config = ModelProfile(**profile).model_dump()
        input_hash = digest({"document": document, "profile": config, "template": "2.0"})
        with self.sessions.begin() as session:
            existing = session.scalar(
                select(RunRow).where(RunRow.idempotency_key == idempotency_key)
            )
            if existing:
                if existing.input_hash != input_hash:
                    raise AppError("idempotency_conflict", "提交标识与输入不一致，请重新提交。")
                return str(existing.id)
            active = session.scalar(
                select(RunRow).where(
                    RunRow.chapter_id == chapter_id,
                    RunRow.status.in_(["pending", "running", "pause_requested", "stop_requested"]),
                )
            )
            if active:
                raise AppError("invalid_state", "本章已有执行中的脚本任务，请完成或暂停后再操作。")
            if source_run_id is not None:
                source = session.get(RunRow, source_run_id)
                if (
                    not source
                    or source.chapter_id != chapter_id
                    or source.revision != source_revision
                ):
                    raise AppError("version_conflict", "编辑依据已更新，请刷新后重新提交。")
            run_id = uid()
            session.add(
                RunRow(
                    id=run_id,
                    chapter_id=chapter_id,
                    idempotency_key=idempotency_key,
                    input_hash=input_hash,
                    document=document,
                    profile=config,
                    result=initial,
                    validation_issues=[],
                )
            )
        return run_id

    def confirm_stage(self, run_id: str, stage: str, revision: int) -> None:
        """Mark the reviewed script as confirmed; the next edit clears the mark."""
        if stage not in STAGES:
            raise AppError("invalid_input", "阶段无效。")
        with self.sessions.begin() as session:
            row = session.get(RunRow, run_id)
            if not row or row.revision != revision:
                raise AppError("version_conflict", "结果已更新，请刷新核对。")
            latest = session.scalar(
                select(RunRow)
                .where(RunRow.chapter_id == row.chapter_id)
                .order_by(RunRow.updated_at.desc())
                .limit(1)
            )
            chapter = session.get(ChapterRow, row.chapter_id)
            if (
                row != latest
                or not chapter
                or any(
                    row.document.get(key, "") != getattr(chapter, key)
                    for key in ("text", "context", "title")
                )
            ):
                raise AppError("version_conflict", "依据已更新，历史结果仅供查看。")
            if (
                row.owner
                or row.status in {"pending", "running", "pause_requested"}
                or stage not in completed_stages(row.result)
            ):
                raise AppError("invalid_state", "脚本尚未生成，不能确认。")
            result = dict(row.result)
            result["confirmed_stages"] = {
                **result.get("confirmed_stages", {}),
                stage: digest(result[stage]),
            }
            row.result = result
            row.revision += 1
            row.updated_at = now()

    def edit_stage(self, run_id: str, stage: str, revision: int, value: dict[str, Any]) -> str:
        if stage != "script":
            raise AppError("invalid_input", "仅支持在此编辑故事脚本。")
        try:
            parsed = StoryScript(**value).model_dump()
        except (ValidationError, TypeError):
            raise AppError("invalid_input", "编辑内容不符合故事脚本的数据格式。") from None
        with self.sessions.begin() as session:
            source = session.get(RunRow, run_id)
            if not source or source.revision != revision:
                raise AppError("version_conflict", "结果已更新，请刷新。")
            latest = session.scalar(
                select(RunRow)
                .where(RunRow.chapter_id == source.chapter_id)
                .order_by(RunRow.updated_at.desc())
                .limit(1)
            )
            chapter = session.get(ChapterRow, source.chapter_id)
            if (
                source != latest
                or not chapter
                or any(
                    source.document.get(key, "") != getattr(chapter, key)
                    for key in ("text", "context", "title")
                )
            ):
                raise AppError("version_conflict", "依据已更新，历史结果不能覆盖当前版本。")
            if source.owner or source.status in {"pending", "running", "pause_requested"}:
                raise AppError("invalid_state", "请等待当前步骤完成后再编辑。")
            if stage not in completed_stages(source.result):
                raise AppError("invalid_state", "脚本尚未生成，不能编辑。")
            result = {
                key: val
                for key, val in source.result.items()
                if key in {"paragraphs", "schema_version", "prompt_template_version"}
            }
            result["script"] = parsed
            result["shots"] = parsed["shots"]
            result["provenance"] = source.result.get("provenance", [])
            result["manual_edit"] = True
            result["validation_issues"] = self._script_issues(result, parsed)
            ident = uid()
            document = {
                **source.document,
                "target_stage": "script",
                "selected_stages": ["script"],
                "source_run_id": run_id,
                "source_revision": revision,
            }
            session.add(
                RunRow(
                    id=ident,
                    chapter_id=source.chapter_id,
                    idempotency_key=uid(),
                    input_hash=digest({"document": document, "result": result}),
                    document=document,
                    profile=source.profile,
                    result=result,
                    status="review_required" if result["validation_issues"] else "succeeded",
                    stage="script",
                    validation_issues=result["validation_issues"],
                )
            )
            return ident

    def save_run_script(self, run_id: str, script: dict[str, Any], expected_revision: int) -> None:
        """Store an edited story script as a new result revision after a full recheck."""
        try:
            parsed = StoryScript(**script).model_dump()
        except (ValidationError, TypeError):
            raise AppError(
                "invalid_input", "脚本行格式不正确，请检查景别、时长必填与空值。"
            ) from None
        with self.sessions.begin() as session:
            row = session.get(RunRow, run_id)
            if not row or row.revision != expected_revision:
                raise AppError("version_conflict", "脚本已更新，请刷新后再保存。")
            if row.status not in ("succeeded", "review_required"):
                raise AppError("invalid_state", "任务尚未完成，请稍后编辑。")
            chapter = session.get(ChapterRow, row.chapter_id)
            if not chapter or any(
                row.document.get(key, "") != getattr(chapter, key)
                for key in ("text", "context", "title")
            ):
                raise AppError("version_conflict", "章节已更新，请重新生成脚本后再修改。")
            result = dict(row.result)
            session.add(
                ResultVersionRow(
                    id=uid(),
                    run_id=run_id,
                    revision=row.revision,
                    result=json.loads(json.dumps(result)),
                )
            )
            issues = self._script_issues(result, parsed)
            result["script"] = parsed
            result["shots"] = parsed["shots"]
            result["manual_edit"] = True
            row.result = result
            row.validation_issues = issues + ["手工修改后需要复核画面连贯与台词还原。"]
            row.status = "review_required"
            row.revision += 1
            row.updated_at = now()

    @staticmethod
    def _script_issues(result: dict[str, Any], script: dict[str, Any]) -> list[str]:
        from novel_anime.domain.validation import validate_script

        try:
            parsed = StoryScript(**script)
            paragraphs = [SourceParagraph(**item) for item in result.get("paragraphs", [])]
        except (ValidationError, TypeError):
            return ["脚本无法通过结构校验，请检查镜头编号、时长与必填内容。"]
        return validate_script(parsed, paragraphs)

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

    def video_prompt(self) -> str:
        """The saved video template, falling back to the shipped Wan2.2 提示词. It only
        supplies style and limits;每镜的画面与动作仍来自脚本。"""
        from novel_anime.core.settings import ROOT

        return self._load_config("media_prompt_templates").get("video") or (
            ROOT / "prompts" / "video.txt"
        ).read_text(encoding="utf-8")

    def save_video_config(self, config: dict[str, Any]) -> None:
        allowed = {
            "base_url",
            "workflow",
            "aspect_ratio",
            "comfy",
            "ratio",
            "workflow_note",
            "preset",
            "target_seconds",
            "max_seconds",
        }
        self._save_config("video", {k: v for k, v in config.items() if k in allowed})

    def character_suggestions(self, work_id: str) -> list[dict[str, Any]]:
        """Characters listed by the newest script and not yet saved as visual profiles."""
        confirmed = {item["character_id"] for item in self.list_visual_profiles(work_id)}
        suggestions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for chapter in self.list_chapters(work_id):
            for run in self.list_runs(chapter["id"]):
                characters = ((run.get("result") or {}).get("script") or {}).get("characters") or []
                if not characters:
                    continue
                for character in characters:
                    try:
                        parsed = ScriptCharacter.model_validate(character)
                    except ValidationError:
                        continue
                    if parsed.character_id in confirmed or parsed.character_id in seen:
                        continue
                    seen.add(parsed.character_id)
                    suggestions.append(
                        {
                            "character_id": parsed.character_id,
                            "name": parsed.name,
                            "appearance": parsed.appearance,
                            "costume": parsed.costume,
                            "source": chapter["title"],
                        }
                    )
                break
        return suggestions

    def adopt_character_suggestions(self, work_id: str) -> int:
        saved = 0
        for item in self.character_suggestions(work_id):
            self.save_visual_profile(
                work_id,
                {
                    "character_id": item["character_id"],
                    "name": item["name"],
                    "appearance": item["appearance"],
                    "costume": item["costume"],
                    "origin": "改编设定",
                    "version": 1,
                    "reference": item["source"],
                },
            )
            saved += 1
        return saved

    def list_visual_profiles(self, work_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            rows = session.scalars(
                select(VisualRow).where(VisualRow.work_id == work_id).order_by(VisualRow.version)
            )
            latest = {row.character_id: row.data for row in rows}
            return [
                {**data, "reference_assets": list(data.get("reference_assets", []))}
                for data in latest.values()
            ]

    def list_character_images(
        self, work_id: str, character_id: str | None = None
    ) -> list[dict[str, Any]]:
        from novel_anime.persistence.database import CharacterImageRow

        with self.sessions() as session:
            query = select(CharacterImageRow).where(CharacterImageRow.work_id == work_id)
            if character_id:
                query = query.where(CharacterImageRow.character_id == character_id)
            return [
                as_dict(row)
                for row in session.scalars(query.order_by(CharacterImageRow.created_at.desc()))
            ]

    def add_character_image(self, work_id: str, metadata: dict[str, Any]) -> None:
        from novel_anime.domain.models import CharacterImageAsset
        from novel_anime.persistence.database import CharacterImageRow

        data = CharacterImageAsset(**metadata).model_dump()
        if data["work_id"] != work_id:
            raise AppError("invalid_input", "素材所属作品不一致。")
        with self.sessions.begin() as session:
            profile = session.scalar(
                select(VisualRow).where(
                    VisualRow.work_id == work_id,
                    VisualRow.character_id == data["character_id"],
                    VisualRow.version == data["profile_version"],
                )
            )
            if profile is None:
                raise AppError("not_found", "角色资料版本不存在，请刷新后重试。")
            session.add(CharacterImageRow(id=uid(), **data))

    def adopt_character_image(
        self, work_id: str, asset_id: str, purpose: str, expected_profile_version: int
    ) -> int:
        from novel_anime.persistence.database import CharacterImageRow

        if purpose not in {"primary", "side", "expression"}:
            raise AppError("invalid_input", "参考素材用途不正确。")
        with self.sessions.begin() as session:
            asset = session.scalar(
                select(CharacterImageRow).where(
                    CharacterImageRow.work_id == work_id,
                    CharacterImageRow.asset_id == asset_id,
                )
            )
            if asset is None:
                raise AppError("not_found", "未找到角色图片素材。")
            previous = session.scalar(
                select(VisualRow)
                .where(VisualRow.work_id == work_id, VisualRow.character_id == asset.character_id)
                .order_by(VisualRow.version.desc())
            )
            if previous is None or previous.version != expected_profile_version:
                raise AppError("version_conflict", "角色资料已更新，请刷新后重新选择素材。")
            next_data = dict(previous.data)
            existing = list(previous.data.get("reference_assets", []))
            if purpose == "primary":
                prior_primary_ids = list(
                    session.scalars(
                        select(CharacterImageRow.asset_id).where(
                            CharacterImageRow.work_id == work_id,
                            CharacterImageRow.character_id == asset.character_id,
                            CharacterImageRow.purpose == "primary",
                        )
                    )
                )
                existing = [item for item in existing if item not in prior_primary_ids]
            if asset_id not in existing:
                existing.append(asset_id)
            new_version = previous.version + 1
            next_data["version"] = new_version
            next_data["reference_assets"] = sorted(set(existing + [asset_id]))
            session.add(
                VisualRow(
                    id=uid(),
                    work_id=work_id,
                    character_id=asset.character_id,
                    version=new_version,
                    data=next_data,
                )
            )
            if purpose == "primary" and existing:
                session.query(CharacterImageRow).filter(
                    CharacterImageRow.work_id == work_id,
                    CharacterImageRow.character_id == asset.character_id,
                    CharacterImageRow.asset_id.in_(existing),
                    CharacterImageRow.purpose == "primary",
                ).update({"purpose": "side"})
            asset.purpose = purpose
            asset.profile_version = new_version
            return new_version

    def delete_character_image(self, work_id: str, asset_id: str) -> None:
        from novel_anime.persistence.database import CharacterImageRow

        with self.sessions.begin() as session:
            asset = session.scalar(
                select(CharacterImageRow).where(
                    CharacterImageRow.work_id == work_id,
                    CharacterImageRow.asset_id == asset_id,
                )
            )
            if asset is None:
                raise AppError("not_found", "未找到角色图片素材。")
            if asset.purpose != "candidate":
                raise AppError("invalid_state", "已采用的参考素材不能删除，请先更新角色资料版本。")
            session.delete(asset)

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
            row = session.get(RunRow, run_id)
            if not row or row.status not in {"pending", "running", "pause_requested"}:
                raise AppError("invalid_state", "当前任务不能暂停。")
            row.status = "pause_requested" if row.owner else "paused"
            row.updated_at = now()

    def stop_run(self, run_id: str) -> None:
        with self.sessions.begin() as session:
            row = session.get(RunRow, run_id)
            if not row or row.status not in {
                "pending",
                "running",
                "paused",
                "pause_requested",
                "stop_requested",
            }:
                raise AppError("invalid_state", "当前任务不能停止。")
            row.status = "stop_requested" if row.owner else "stopped"
            row.updated_at = now()

    def resume_run(self, run_id: str, stage_only: bool = False) -> None:
        with self.sessions.begin() as session:
            row = session.get(RunRow, run_id)
            if not row or row.status not in {"paused", "pause_requested"}:
                raise AppError("invalid_state", "当前任务不能继续。")
            if row.owner:
                if row.status != "pause_requested":
                    raise AppError("invalid_state", "执行器尚未释放任务，请稍后重试。")
                row.status = "running"
                row.updated_at = now()
                return
            if stage_only:
                self._single_stage_retry(session, run_id)
            session.execute(
                update(RunRow)
                .where(
                    RunRow.id == run_id,
                    RunRow.status.in_(["paused", "pause_requested"]),
                    RunRow.owner.is_(None),
                )
                .values(status="pending", updated_at=now())
            )

    def retry_run(self, run_id: str, stage_only: bool = False) -> None:
        with self.sessions.begin() as session:
            if stage_only:
                self._single_stage_retry(session, run_id)
            session.execute(
                update(RunRow)
                .where(
                    RunRow.id == run_id,
                    RunRow.status.in_(["failed", "unknown", "stopped"]),
                    RunRow.owner.is_(None),
                )
                .values(status="pending", error_message=None, updated_at=now())
            )

    def _single_stage_retry(self, session: Any, run_id: str) -> None:
        row = session.get(RunRow, run_id)
        if (
            not row
            or row.owner
            or row.status not in {"failed", "unknown", "paused", "pause_requested", "stopped"}
        ):
            raise AppError("invalid_state", "此任务当前不能继续。")
        latest = session.scalar(
            select(RunRow)
            .where(RunRow.chapter_id == row.chapter_id)
            .order_by(RunRow.updated_at.desc())
            .limit(1)
        )
        chapter = session.get(ChapterRow, row.chapter_id)
        if (
            row != latest
            or not chapter
            or any(
                row.document.get(key, "") != getattr(chapter, key)
                for key in ("text", "context", "title")
            )
        ):
            raise AppError("version_conflict", "章节已更新，请从当前版本重新生成脚本。")
        row.document = {**row.document, "target_stage": "script", "selected_stages": ["script"]}
