from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select, update

from novel_anime.core.errors import AppError
from novel_anime.domain.models import Character, InputDocument, ModelProfile, Shot, VisualProfile
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


STAGES = ("analysis", "outline", "shots", "review")


def completed_stages(result: dict[str, Any]) -> list[str]:
    completed = [stage for stage in STAGES if result.get(stage)]
    batches = result.get("shot_batches", {})
    scenes = result.get("outline", {}).get("scenes", [])
    if batches and (len(batches) < len(scenes) or result.get("shot_pending")):
        completed = [stage for stage in completed if stage not in {"shots", "review"}]
    return completed


def as_dict(row: Any) -> dict[str, Any]:
    value = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    if isinstance(row, RunRow):
        result = dict(value["result"])
        if not result.get("shots") and result.get("shot_batches"):
            result["shots"] = [shot for batch in result["shot_batches"].values() for shot in batch]
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
        run = next((item for item in runs if item.get("result", {}).get("shots")), None)
        if run is None:
            return {"shots": [], "run": None}
        profiles = {p["character_id"]: p for p in self.list_visual_profiles(chapter["work_id"])}
        assets = self.list_character_images(chapter["work_id"])
        characters = {
            p["character_id"]: p for p in run["result"].get("analysis", {}).get("characters", [])
        }
        snapshots = {p["character_id"]: p for p in run["document"].get("visual_profiles", [])}
        shots = []
        for shot in sorted(run["result"]["shots"], key=lambda item: item["order"]):
            references = []
            subjects = []
            appearance_prompts = {
                p["character_id"]: p["prompt"] for p in shot.get("character_prompts", [])
            }
            for character_id in dict.fromkeys(shot.get("character_ids", [])):
                profile = profiles.get(character_id, {})
                character = characters.get(character_id, {})
                snapshot = snapshots.get(character_id, {})
                subjects.append(
                    {
                        "character_id": character_id,
                        "name": character.get("name")
                        or snapshot.get("name")
                        or profile.get("name", character_id),
                        "description": appearance_prompts.get(character_id, ""),
                        "images": [
                            {"asset_id": asset["asset_id"]}
                            for asset in assets
                            if asset["character_id"] == character_id
                            and asset["asset_id"] in set(profile.get("reference_assets", []))
                            and asset["purpose"] != "candidate"
                        ],
                    }
                )
                adopted = set(profile.get("reference_assets", []))
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

    def shot_image_source(self, run_id: str, shot_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        image_run_id = run["document"].get("shot_image_run_id", run_id)
        result = run["result"]
        shot = next((s for s in result.get("shots", []) if s["shot_id"] == shot_id), None)
        if shot is None:
            raise AppError("not_found", "分镜不存在，请重新选择。")
        character_ids = set(shot.get("character_ids", []))
        profiles = [
            p
            for p in run["document"].get("visual_profiles", [])
            if p["character_id"] in character_ids
        ]
        scene = next(
            (
                s
                for s in result.get("outline", {}).get("scenes", [])
                if s["scene_id"] == shot.get("scene_id")
            ),
            {},
        )
        individual = {
            item["character_id"]: item["prompt"]
            for prior in sorted(result.get("shots", []), key=lambda value: value["order"])
            if prior["order"] <= shot["order"]
            for item in prior.get("character_prompts", [])
            if item.get("prompt")
        }
        character_text = (
            "\n".join(individual[cid] for cid in shot.get("character_ids", []) if cid in individual)
            or shot.get("character_prompt")
            or "\n".join(
                f"{p['name']}：外貌 {p.get('appearance', '未知')}；服装 {p.get('costume', '未知')}"
                for p in profiles
            )
            or ("无人物" if not character_ids else "本版分镜未记录人物外观，请重新生成分镜。")
        )
        environment = (
            shot.get("environment_prompt")
            or "\n".join(str(scene.get(key, "")) for key in ("location", "time") if scene.get(key))
            or "本版分镜未记录环境描述，请先在分镜拆解中补充。"
        )
        content = {
            "character_prompt": character_text,
            "environment_prompt": environment,
            "shot_prompt": "\n".join(
                str(shot.get(key, ""))
                for key in (
                    "title",
                    "shot_size",
                    "camera",
                    "composition",
                    "action",
                    "start_state",
                    "end_state",
                )
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
        names = {
            p["character_id"]: p["name"] for p in result.get("analysis", {}).get("characters", [])
        }
        profile_by_id = {p["character_id"]: p for p in profiles}
        subjects = []
        for cid in shot.get("character_ids", []):
            profile = profile_by_id.get(cid, {})
            subjects.append(
                {
                    "character_id": cid,
                    "name": names.get(cid) or profile.get("name", cid),
                    "prompt": individual.get(cid)
                    or "；".join(filter(None, [profile.get("appearance"), profile.get("costume")]))
                    or (shot.get("character_prompt", "") if len(character_ids) == 1 else ""),
                }
            )
        return {**content, "subjects": subjects, "source_hash": source_hash, "images": images}

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
        if selected_stages is not None:
            if not isinstance(selected_stages, list) or not selected_stages:
                raise AppError("invalid_input", "请选择要执行的相邻步骤。")
            if any(stage not in STAGES for stage in selected_stages):
                raise AppError("invalid_input", "执行阶段无效。")
            first = STAGES.index(selected_stages[0])
            if list(STAGES[first : first + len(selected_stages)]) != selected_stages:
                raise AppError("invalid_input", "只能合并连续相邻的步骤。")
            target_stage = selected_stages[0]
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
            stage: saved_templates.get(stage)
            or (ROOT / "prompts" / f"{stage}.txt").read_text(encoding="utf-8")
            for stage in ("analysis", "outline", "shots", "review")
        }
        initial: dict[str, Any] = {}
        if target_stage is not None:
            if target_stage not in STAGES:
                raise AppError("invalid_input", "请选择分析、大纲、分镜或复核。")
            document["target_stage"] = target_stage
            if selected_stages is not None:
                document["selected_stages"] = selected_stages
            document["chapter_revision"] = chapter["revision"]
            document["source_run_id"] = source_run_id
            document["source_revision"] = source_revision
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
            if target_stage is not None:
                active = session.scalar(
                    select(RunRow).where(
                        RunRow.chapter_id == chapter_id,
                        RunRow.status.in_(
                            ["pending", "running", "pause_requested", "stop_requested"]
                        ),
                    )
                )
                if active:
                    raise AppError("invalid_state", "本章已有执行中的步骤，请完成或暂停后再操作。")
                source = session.get(RunRow, source_run_id) if source_run_id else None
                latest = session.scalar(
                    select(RunRow)
                    .where(RunRow.chapter_id == chapter_id)
                    .order_by(RunRow.updated_at.desc())
                    .limit(1)
                )
                if source_run_id and (
                    not source
                    or source.chapter_id != chapter_id
                    or source.revision != source_revision
                    or source != latest
                ):
                    raise AppError("version_conflict", "执行依据已更新，请刷新后选择当前版本。")
                index = STAGES.index(target_stage)
                if index:
                    if (
                        not source
                        or source.owner
                        or source.status in {"running", "pending", "pause_requested"}
                    ):
                        raise AppError("invalid_state", "请先完成并确认上一阶段。")
                    if any(
                        source.document.get(key, "") != chapter[key]
                        for key in ("text", "context", "title")
                    ):
                        raise AppError("version_conflict", "章节已更新，请重新分析；旧结果仍保留。")
                    approved = source.result.get("confirmed_stages", {})
                    for prior in STAGES[:index]:
                        if prior not in completed_stages(source.result) or approved.get(
                            prior
                        ) != digest(source.result[prior]):
                            raise AppError(
                                "invalid_state", "请先确认此前阶段的结果，再进入下一步。"
                            )
                    keep = {
                        "paragraphs",
                        "schema_version",
                        "prompt_template_version",
                        "generation_status",
                    }
                    keep.update(STAGES[:index])
                    if index > 2:
                        keep.add("shot_batches")
                        document["shot_image_run_id"] = source.document.get(
                            "shot_image_run_id", source.id
                        )
                    initial = {key: value for key, value in source.result.items() if key in keep}
                    initial["confirmed_stages"] = {key: approved[key] for key in STAGES[:index]}
                    initial["provenance"] = [
                        item
                        for item in source.result.get("provenance", [])
                        if item.get("stage") in STAGES[:index]
                    ]
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
                raise AppError("invalid_state", "该阶段尚未完成，不能确认。")
            result = dict(row.result)
            result["confirmed_stages"] = {
                **result.get("confirmed_stages", {}),
                stage: digest(result[stage]),
            }
            row.result = result
            row.revision += 1
            row.updated_at = now()

    def edit_stage(self, run_id: str, stage: str, revision: int, value: dict[str, Any]) -> str:
        from novel_anime.domain.models import Analysis, Outline

        schemas = {"analysis": Analysis, "outline": Outline}
        if stage not in schemas:
            raise AppError("invalid_input", "仅支持在此编辑分析或大纲。")
        try:
            parsed = schemas[stage](**value).model_dump()
        except (ValidationError, TypeError):
            raise AppError("invalid_input", "编辑内容不符合本阶段的数据格式。") from None
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
            if (
                source.owner
                or source.status in {"pending", "running", "pause_requested"}
                or stage not in completed_stages(source.result)
            ):
                raise AppError("invalid_state", "请等待当前步骤完成后再编辑。")
            known_paragraphs = {p["paragraph_id"] for p in source.result.get("paragraphs", [])}
            if stage == "analysis":
                if any(
                    not set(item.get("source_paragraph_ids", [])).issubset(known_paragraphs)
                    for key in ("characters", "events")
                    for item in parsed[key]
                ):
                    raise AppError("invalid_input", "分析引用了不存在的原文段落。")
            elif not parsed["scenes"] or any(
                not set(scene["event_ids"]).issubset(
                    {event["event_id"] for event in source.result["analysis"]["events"]}
                )
                for scene in parsed["scenes"]
            ):
                raise AppError("invalid_input", "大纲需要场景，且只能引用已分析的事件。")
            keep = {
                "paragraphs",
                "schema_version",
                "prompt_template_version",
                "generation_status",
                *STAGES[: STAGES.index(stage)],
            }
            result = {key: val for key, val in source.result.items() if key in keep}
            result[stage] = parsed
            result["confirmed_stages"] = {
                key: val
                for key, val in source.result.get("confirmed_stages", {}).items()
                if key in STAGES[: STAGES.index(stage)]
            }
            result["provenance"] = source.result.get("provenance", [])
            result["manual_edit"] = True
            ident = uid()
            document = {
                **source.document,
                "target_stage": stage,
                "selected_stages": [stage],
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
                    status="stage_complete",
                    stage=stage,
                    validation_issues=[],
                )
            )
            return ident

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

    def character_suggestions(self, work_id: str) -> list[dict[str, Any]]:
        """Characters named by the latest analysis and not yet saved as visual profiles."""
        confirmed = {item["character_id"] for item in self.list_visual_profiles(work_id)}
        suggestions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for chapter in self.list_chapters(work_id):
            for run in self.list_runs(chapter["id"]):
                characters = ((run.get("result") or {}).get("analysis") or {}).get(
                    "characters"
                ) or []
                if not characters:
                    continue
                for character in characters:
                    try:
                        parsed = Character.model_validate(character)
                    except ValidationError:
                        continue
                    if parsed.character_id in confirmed or parsed.character_id in seen:
                        continue
                    seen.add(parsed.character_id)
                    suggestions.append(
                        {
                            "character_id": parsed.character_id,
                            "name": parsed.name,
                            "appearance": parsed.description,
                            "costume": "本章分析未分开描述服装",
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
            raise AppError("version_conflict", "上游已更新，请从当前版本执行下一步。")
        stage = row.document.get("target_stage") or row.stage.split()[0]
        row.document = {**row.document, "target_stage": stage if stage in STAGES else "analysis"}
