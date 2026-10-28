"""Curated prompt snapshots, image references and imported video files."""

from __future__ import annotations

import base64
import hashlib
import re
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from novel_anime.application.service import StudioService, as_dict
from novel_anime.core.errors import AppError
from novel_anime.core.settings import get_settings
from novel_anime.persistence.database import (
    GalleryPersonRow,
    LibraryEntryRow,
    ShotImageRow,
    StudioImageRow,
    WorkRow,
    now,
)

MAX_VIDEO_BYTES = 48_000_000


def list_people(service: StudioService, work_id: str) -> list[dict[str, Any]]:
    with service.sessions() as session:
        if session.get(WorkRow, work_id) is None:
            raise AppError("not_found", "作品不存在。")
        return [as_dict(row) for row in session.scalars(
            select(GalleryPersonRow).where(GalleryPersonRow.work_id == work_id)
            .order_by(GalleryPersonRow.created_at, GalleryPersonRow.id)
        )]


def create_person(service: StudioService, body: dict) -> dict:
    work_id, name = str(body["work_id"]), body.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        raise AppError("invalid_input", "请填写 1 到 120 字的人物名称。")
    name = name.strip()
    try:
        with service.sessions.begin() as session:
            if session.get(WorkRow, work_id) is None:
                raise AppError("not_found", "作品不存在。")
            old = session.scalar(select(GalleryPersonRow).where(
                GalleryPersonRow.work_id == work_id, GalleryPersonRow.name == name
            ))
            if old:
                return {**as_dict(old), "existing": True}
            row = GalleryPersonRow(id=uuid4().hex, work_id=work_id, name=name, created_at=now())
            session.add(row)
            result = as_dict(row)
    except IntegrityError:
        with service.sessions() as session:
            old = session.scalar(select(GalleryPersonRow).where(
                GalleryPersonRow.work_id == work_id, GalleryPersonRow.name == name
            ))
            if old is None:
                raise
            return {**as_dict(old), "existing": True}
    return {**result, "existing": False}


def keyframe_prompt(
    shot: dict[str, Any],
    characters: list[dict[str, Any]],
    style: str = "",
    aspect_ratio: str = "16:9",
) -> str:
    """Describe one still frame using the saved shot, without dialogue or video timing."""

    def clean(value: Any) -> str:
        return str(value or "").strip().rstrip("。！？.!?；;")

    by_id = {str(person["character_id"]): person for person in characters}
    people = []
    for ident in dict.fromkeys(shot.get("character_ids", [])):
        person = by_id.get(str(ident), {})
        description = "，".join(
            str(person.get(key) or "")
            for key in ("name", "appearance", "costume")
            if person.get(key)
        )
        people.append(description or str(ident))
    parts = ["生成此分镜的一张起始关键帧图片，静态单幅画面，可用于图生视频。"]
    if style:
        parts.append(f"画风：{style}。")
    parts.append(f"画幅：{aspect_ratio}。")
    if shot.get("shot_size"):
        parts.append(f"景别：{shot['shot_size']}。")
    if shot.get("setting"):
        parts.append(f"场景与环境：{clean(shot['setting'])}。")
    if people:
        parts.append("出场人物与造型：" + "；".join(people) + "。")
    if shot.get("frame"):
        parts.append(f"画面与构图：{clean(shot['frame'])}。")
    visible_action = "，".join(
        part.strip()
        for part in re.split(r"[，,。；;\n]", str(shot.get("action") or ""))
        if part.strip()
        and not re.search(r"声音|响|鸣|台词|对白|音效|说|喊|叫|听|念|唱|[“”「」]", part)
    )
    if visible_action:
        parts.append(f"可见动作定格参考：{clean(visible_action)}。仅表现动作开始时的一个定格瞬间。")
    parts.append(
        "沿用本镜头描述的主体位置、环境、光线与人物关系，保持角色外貌和服装一致。"
        "只绘制分镜中可见的内容，不添加无关人物或道具。"
    )
    parts.append("画面清晰，主体完整，单张画面，不要拼图、分格、字幕、文字、水印或对白气泡。")
    return "\n".join(parts)


def prompt_groups(
    script: dict[str, Any], config: dict, style: str, image_style: str = ""
) -> list[dict[str, Any]]:
    """Use the same grouping and prompt compiler as video production, without a model call."""
    from novel_anime.domain.video import compile_plan, shot_prompt

    shots = script.get("shots", [])
    characters = script.get("characters", [])
    groups = compile_plan(shots, characters, style, config)["groups"]
    names = {
        str(person["character_id"]): "；".join(
            str(person.get(key) or "")
            for key in ("name", "appearance", "costume")
            if person.get(key)
        )
        for person in characters
    }
    by_id = {str(shot["shot_id"]): shot for shot in shots}
    for group in groups:
        group["shot_prompts"] = [
            {
                "shot_id": ident,
                "duration_seconds": by_id[ident].get("duration_seconds", 0),
                "prompt": shot_prompt(by_id[ident], names),
                "keyframe_prompt": keyframe_prompt(
                    by_id[ident], characters, image_style, str(config.get("aspect_ratio") or "16:9")
                ),
            }
            for ident in group["shot_ids"]
        ]
    return groups


def list_entries(service: StudioService, work_id: str) -> list[dict[str, Any]]:
    with service.sessions() as session:
        if session.get(WorkRow, work_id) is None:
            raise AppError("not_found", "作品不存在。")
        entries = [
            as_dict(row)
            for row in session.scalars(
                select(LibraryEntryRow)
                .where(LibraryEntryRow.work_id == work_id)
                .order_by(LibraryEntryRow.created_at.desc(), LibraryEntryRow.id)
            )
        ]
    # Older collections only stored scripts; derive their groups from that saved script.
    for entry in entries:
        content = entry["content"]
        if entry["kind"] == "prompts" and "groups" not in content:
            content["groups"] = prompt_groups(content.get("script", {}), {}, "")
        if entry["kind"] == "prompts":
            script = content.get("script", {})
            by_id = {str(shot["shot_id"]): shot for shot in script.get("shots", [])}
            for group in content["groups"]:
                for shot in group.get("shot_prompts", []):
                    if "keyframe_prompt" not in shot:
                        shot["keyframe_prompt"] = keyframe_prompt(
                            by_id.get(str(shot["shot_id"]), {}),
                            script.get("characters", []),
                            content.get("image_style", ""),
                            content.get("aspect_ratio", "16:9"),
                        )

    return entries


def save_entry(service: StudioService, body: dict) -> dict:
    work_id, kind = str(body["work_id"]), str(body["kind"])
    with service.sessions() as session:
        if session.get(WorkRow, work_id) is None:
            raise AppError("not_found", "作品不存在。")
    chapter_id = ""
    if kind == "prompts":
        run = service.get_run(str(body["run_id"]))
        chapter = service.get_chapter(run["chapter_id"])
        if "revision" in body and int(body["revision"]) != run["revision"]:
            raise AppError("version_conflict", "脚本版本已更新，请刷新后再收藏。")
        if chapter["work_id"] != work_id:
            raise AppError("invalid_input", "分镜不属于当前作品。")
        script = run["result"].get("script") or {}
        if not script.get("shots"):
            raise AppError("invalid_input", "请先生成故事脚本。")
        chapter_id = chapter["id"]
        key = f"{run['id']}:{run['revision']}"
        video_config = service.load_video_config()
        image_style = str(run["document"].get("style") or "")
        aspect_ratio = str(
            video_config.get("aspect_ratio") or run["document"].get("aspect_ratio") or "16:9"
        )
        content = {
            "image_style": image_style,
            "aspect_ratio": aspect_ratio,
            "chapter_title": chapter["title"],
            "script": script,
            "run_id": run["id"],
            "revision": run["revision"],
            "generated_at": run["updated_at"],
            "groups": prompt_groups(
                script,
                {**video_config, "aspect_ratio": aspect_ratio},
                service.video_prompt(),
                image_style,
            ),
        }
    elif kind == "images":
        asset_id = str(body["asset_id"])
        with service.sessions() as session:
            image = session.get(ShotImageRow, asset_id)
            image = as_dict(image) if image else None
        if image:
            run = service.get_run(image["run_id"])
            chapter = service.get_chapter(run["chapter_id"])
            if chapter["work_id"] != work_id:
                raise AppError("invalid_input", "图片不属于当前作品。")
            chapter_id = chapter["id"]
            subject = image["subject_id"]
            people = run["result"].get("script", {}).get("characters", [])
            person: dict[str, Any] = next((p for p in people if p["character_id"] == subject), {})
            label = person.get("name") or subject or "未命名人物"
            if image["kind"] != "character":
                label = "环境与场景" if image["kind"] == "environment" else "分镜画面"
            source_path = get_settings().data_dir / "assets" / "shots" / image["filename"]
            content = {
                "asset_id": asset_id,
                "url": f"/assets/shots/{asset_id}.img",
                "group_key": image["kind"] + ":" + subject,
                "name": label,
                "prompt": image["prompt"],
                "image_kind": image["kind"],
            }
        else:
            with service.sessions() as session:
                studio = session.get(StudioImageRow, asset_id)
                studio = as_dict(studio) if studio else None
            if studio:
                image_kind = str(body.get("image_kind", "character"))
                label = str(body.get("name", "")).strip()
                if image_kind not in {"character", "environment", "composition"}:
                    raise AppError("invalid_input", "请选择人物、环境或分镜画面。")
                if not label or len(label) > 120:
                    raise AppError("invalid_input", "请填写 1 到 120 字的人物或素材名称。")
                source_path = get_settings().data_dir / "assets" / "studio" / studio["filename"]
                content = {
                    "asset_id": asset_id,
                    "group_key": image_kind + ":" + label,
                    "name": label,
                    "image_kind": image_kind,
                    "prompt": studio["prompt"],
                }
            else:
                image = next(
                    (
                        item
                        for item in service.list_character_images(work_id)
                        if item["asset_id"] == asset_id
                    ),
                    None,
                )
                if image is None:
                    raise AppError("not_found", "图片不存在或不属于当前作品。")
                person = next(
                    (
                        p
                        for p in service.list_visual_profiles(work_id)
                        if p["character_id"] == image["character_id"]
                    ),
                    {},
                )
                source_path = (
                    get_settings().data_dir
                    / "assets"
                    / "characters"
                    / work_id
                    / image["character_id"]
                    / image["filename"]
                )
                content = {
                    "asset_id": asset_id,
                    "url": f"/assets/characters/{asset_id}.img",
                    "group_key": "character:" + image["character_id"],
                    "name": person.get("name") or image["character_id"],
                    "image_kind": "character",
                    "prompt": "",
                }
        person_id = body.get("person_id")
        if person_id:
            with service.sessions() as session:
                selected_person = session.get(GalleryPersonRow, str(person_id))
                if selected_person is None or selected_person.work_id != work_id:
                    raise AppError("invalid_input", "请选择当前作品的图库人物。")
                content.update(
                    name=selected_person.name, image_kind="character",
                    group_key="character:" + selected_person.name,
                )
        root = (get_settings().data_dir / "assets").resolve()
        source_path = source_path.resolve()
        if root not in source_path.parents or not source_path.is_file():
            raise AppError("not_found", "图片文件不存在，请重新生成或上传。")
        raw = source_path.read_bytes()
        extension = ".png" if raw.startswith(b"\x89PNG") else ".jpg"
        filename = hashlib.sha256(raw).hexdigest() + extension
        archive = root / "library_images"
        archive.mkdir(parents=True, exist_ok=True)
        target = archive / filename
        if not target.exists():
            temp = archive / (uuid4().hex + ".tmp")
            temp.write_bytes(raw)
            temp.replace(target)
        content["url"] = "/assets/library-images/" + filename
        if content["image_kind"] == "character":
            gallery_person = create_person(service, {"work_id": work_id, "name": content["name"]})
            content["person_id"] = gallery_person["id"]
        key = asset_id
    elif kind == "videos":
        chapter = service.get_chapter(str(body["chapter_id"]))
        if chapter["work_id"] != work_id:
            raise AppError("invalid_input", "章节不属于当前作品。")
        chapter_id = chapter["id"]
        title = str(body.get("title") or "未命名视频").strip()
        if not title or len(title) > 160:
            raise AppError("invalid_input", "视频名称需为 1 到 160 字。")
        url = str(body.get("url", "")).strip()
        video_data = body.get("data_url")
        if video_data:
            match = re.fullmatch(r"data:video/(mp4|webm);base64,([A-Za-z0-9+/=]+)", str(video_data))
            if not match or len(match[2]) > MAX_VIDEO_BYTES * 4 // 3 + 8:
                raise AppError("invalid_input", "请导入 48 MB 以内的 MP4 或 WebM 视频。")
            try:
                raw = base64.b64decode(match[2], validate=True)
            except ValueError as exc:
                raise AppError("invalid_input", "视频文件无效。") from exc
            valid = (match[1] == "mp4" and raw[4:8] == b"ftyp") or (
                match[1] == "webm" and raw[:4] == b"\x1aE\xdf\xa3"
            )
            if not valid or not 12 <= len(raw) <= MAX_VIDEO_BYTES:
                raise AppError("invalid_input", "视频内容与格式不一致。")
            file_id = hashlib.sha256(raw).hexdigest() + "." + match[1]
            root = get_settings().data_dir / "assets" / "library_videos"
            root.mkdir(parents=True, exist_ok=True)
            target = root / file_id
            if not target.exists():
                temp = root / (uuid4().hex + ".tmp")
                temp.write_bytes(raw)
                temp.replace(target)
            url = "/assets/library-videos/" + file_id
        else:
            parsed = urlparse(url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or len(url) > 4096:
                raise AppError("invalid_input", "请填写可直接播放的 HTTP 或 HTTPS 视频地址。")
            if parsed.username or parsed.password:
                raise AppError("invalid_input", "视频地址不能包含账号密码。")
        key = hashlib.sha256((chapter_id + ":" + url).encode()).hexdigest()
        content = {"chapter_title": chapter["title"], "title": title, "url": url}
    else:
        raise AppError("invalid_input", "不支持的资料类型。")
    ident = uuid4().hex
    try:
        with service.sessions.begin() as session:
            old = session.scalar(
                select(LibraryEntryRow).where(
                    LibraryEntryRow.work_id == work_id,
                    LibraryEntryRow.kind == kind,
                    LibraryEntryRow.source_key == key,
                )
            )
            if old:
                return {"id": old.id, "existing": True}
            session.add(
                LibraryEntryRow(
                    id=ident,
                    work_id=work_id,
                    kind=kind,
                    chapter_id=chapter_id,
                    source_key=key,
                    created_at=now(),
                    content=content,
                )
            )
    except IntegrityError:
        # Two browser tabs may collect the same output at once.
        with service.sessions() as session:
            old = session.scalar(
                select(LibraryEntryRow).where(
                    LibraryEntryRow.work_id == work_id,
                    LibraryEntryRow.kind == kind,
                    LibraryEntryRow.source_key == key,
                )
            )
            if old is None:
                raise
            return {"id": old.id, "existing": True}
    return {"id": ident, "existing": False}


def remove_entry(service: StudioService, work_id: str, ident: str) -> dict:
    with service.sessions.begin() as session:
        row = session.get(LibraryEntryRow, ident)
        if row is None or row.work_id != work_id:
            raise AppError("not_found", "收藏不存在。")
        session.delete(row)
    return {"removed": True}
