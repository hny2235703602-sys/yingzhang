"""Same-origin HTML interface using the existing service and independent worker."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from novel_anime.application.service import StudioService
from novel_anime.core.credentials import resolve_key, store_key
from novel_anime.core.errors import AppError
from novel_anime.core.settings import get_settings
from novel_anime.domain.models import ModelProfile
from novel_anime.persistence.database import ConfigRow, WorkRow, now
from novel_anime.ui.export import safe_payload

MAX_IMAGE_BYTES = 8_000_000
IMAGE_DATA_URL = re.compile(r"^data:(image/(?:png|jpeg));base64,([A-Za-z0-9+/=]+)$")

STATIC = Path(__file__).with_name("web")
LOG = logging.getLogger(__name__)


def snapshot_image_prompt(service: StudioService) -> str:
    from novel_anime.core.settings import ROOT

    return service._load_config("media_prompt_templates").get("image") or (
        ROOT / "prompts" / "shot_image.txt"
    ).read_text(encoding="utf-8")


def snapshot(service: StudioService) -> dict:
    with service.sessions() as session:
        row = session.get(ConfigRow, "web_metadata")
        metadata = row.value if row else {}
    works = []
    for work in service.list_works():
        extra = metadata.get(work["id"], {})
        works.append(
            {
                **extra,
                **work,
                "style": extra.get("style", "国风动画"),
                "updated": extra.get("updated", ""),
                "characters": [
                    {**profile, "source": profile.get("reference") or profile.get("source") or ""}
                    for profile in service.list_visual_profiles(work["id"])
                ],
                "character_images": service.list_character_images(work["id"]),
                "character_suggestions": service.character_suggestions(work["id"]),
                "chapters": service.list_chapters(work["id"]),
            }
        )
    profiles = []
    for key, p in service.load_profiles().items():
        stored = resolve_key(p["api_key_env"])
        profiles.append(
            {
                **p,
                "id": key,
                "name": p["provider"],
                "url": p["base_url"],
                "env": p["api_key_env"],
                "tokens": p["max_output_tokens"],
                "key_configured": bool(stored),
                "key_tail": stored[-4:] if len(stored) > 4 else "",
            }
        )
    from novel_anime.core.settings import ROOT

    saved = service._load_config("prompt_templates")
    templates = {
        stage: saved.get(stage) or (ROOT / "prompts" / f"{stage}.txt").read_text(encoding="utf-8")
        for stage in ("analysis", "outline", "shots", "review")
    }
    image_profile = service._load_config("image_profile")
    saved_media = service._load_config("media_prompt_templates")
    media_templates = {
        kind: saved_media.get(kind) or (ROOT / "prompts" / filename).read_text(encoding="utf-8")
        for kind, filename in (("image", "shot_image.txt"), ("video", "video.txt"))
    }
    if image_profile:
        key = resolve_key(image_profile.get("api_key_env", ""))
        image_profile = {
            **image_profile,
            "key_configured": bool(key),
            "key_tail": key[-4:] if len(key) > 4 else "",
        }
    return {
        "novels": works,
        "profiles": profiles,
        "image_profile": image_profile,
        "templates": templates,
        "media_templates": media_templates,
    }


def dispatch(service: StudioService, action: str, body: dict) -> dict:
    if action == "edit-stage":
        ident = service.edit_stage(
            str(body["run_id"]), str(body["stage"]), int(body["revision"]), body["value"]
        )
        return {"id": ident}
    if action == "confirm-stage":
        service.confirm_stage(str(body["run_id"]), str(body["stage"]), int(body["revision"]))
        return {"confirmed": True}
    if action == "select-shot-image":
        service.select_shot_image(str(body["run_id"]), str(body["shot_id"]), str(body["asset_id"]))
        return {"selected": True}
    if action in {"generate-shot-image", "upload-shot-image"}:
        from novel_anime.integrations.image_generation.client import ImageGenerationClient

        run_id, shot_id = str(body["run_id"]), str(body["shot_id"])
        source = service.shot_image_source(run_id, shot_id)
        if body.get("source_hash") != source["source_hash"]:
            raise AppError("version_conflict", "分镜已修改，请刷新后重新生成。")
        global_prompt = snapshot_image_prompt(service)
        if body.get("global_prompt") != global_prompt:
            raise AppError("version_conflict", "全局提示词已修改，请刷新工作台后生成。")
        extra = body.get("extra", "")
        count = body.get("count", 1)
        if (
            not isinstance(extra, str)
            or len(extra) > 2000
            or type(count) is not int
            or not 1 <= count <= 4
        ):
            raise AppError("invalid_input", "补充要求最多 2000 字，单次生成 1 到 4 张。")
        kind = body.get("kind", "composition")
        subject_id = body.get("subject_id", "")
        if kind not in {"character", "environment", "composition"} or not isinstance(
            subject_id, str
        ):
            raise AppError("invalid_input", "请选择角色、环境或整体画面。")
        subject = next(
            (item for item in source["subjects"] if item["character_id"] == subject_id), None
        )
        if kind == "character" and not subject:
            raise AppError("invalid_input", "请选择当前分镜中的角色。")
        if kind != "character":
            subject_id = ""
        ids = body.get("reference_assets", [])
        if (
            not isinstance(ids, list)
            or len(ids) > 6
            or any(not isinstance(item, str) for item in ids)
            or len(ids) != len(set(ids))
        ):
            raise AppError("invalid_input", "最多选择 6 张不同的参考图。")
        available = {item["asset_id"]: item for item in source["images"]}
        if any(ident not in available for ident in ids):
            raise AppError("version_conflict", "参考图不属于当前分镜版本，请刷新。")
        root = (get_settings().data_dir / "assets" / "shots").resolve()
        references = []
        for ident in ids:
            item = available[ident]
            path = (root / item["filename"]).resolve()
            if root not in path.parents or not path.is_file():
                raise AppError("not_found", "参考图文件不存在，请重新上传。")
            references.append((path.read_bytes(), item["mime_type"]))
        if kind == "character":
            content = (
                "【角色外观】\n"
                + subject["prompt"]
                + "\n只绘制此角色的独立全身形象，简洁背景，不绘制其他角色或故事场景。"
            )
        elif kind == "environment":
            content = (
                "【环境描述】\n"
                + source["environment_prompt"]
                + "\n只绘制独立环境场景，不出现人物。"
            )
        else:
            content = (
                "【人物描述】\n"
                + source["character_prompt"]
                + "\n【环境描述】\n"
                + source["environment_prompt"]
                + "\n【镜头内容】\n"
                + source["shot_prompt"]
            )
        reference_notes = []
        figure_names = ["图一", "图二", "图三", "图四", "图五", "图六"]
        if action == "generate-shot-image":
            mentioned = re.findall(r"图\s*([一二三四五六七八九十]|\d+)", extra)
            for mention in mentioned:
                number = (
                    int(mention) if mention.isdigit() else "一二三四五六七八九十".index(mention) + 1
                )
                if not 1 <= number <= len(ids):
                    raise AppError(
                        "invalid_input", "补充要求引用了未选中的参考图，请核对图片编号。"
                    )
        for index, ident in enumerate(ids, 1):
            item = available[ident]
            name = next(
                (
                    person["name"]
                    for person in source["subjects"]
                    if person["character_id"] == item["subject_id"]
                ),
                "",
            )
            role = {
                "character": "角色外观 " + name,
                "environment": "环境空间与陈设",
                "composition": "整体构图",
            }[item["kind"]]
            reference_notes.append(
                f"参考图{index}用于参考{role}，不照搬无关内容。"
                f"此图编号为「{figure_names[index - 1]}」，即本次输入的第{index}张图片；"
                f"补充要求中的「{figure_names[index - 1]}」「图{index}」均指此图。"
            )
        prompt = "\n\n".join(
            [
                "【全局风格与用途】\n" + global_prompt,
                "【本次产物】\n"
                + {
                    "character": "独立角色图",
                    "environment": "独立无人物环境图",
                    "composition": "完整分镜画面",
                }[kind]
                + "。全局配置用于统一画风与用途；具体主体与构图以本次产物和以下内容为准。",
                content,
                "【参考图用途】\n" + "\n".join(reference_notes),
                "【本次补充】\n" + extra.strip(),
            ]
        )
        if len(prompt) > 40000:
            raise AppError("invalid_input", "合成提示词过长，请缩短描述。")
        if action == "upload-shot-image":
            match = IMAGE_DATA_URL.fullmatch(str(body.get("data_url", "")))
            if not match:
                raise AppError("invalid_input", "请上传 PNG 或 JPEG 图片。")
            try:
                image_bytes = base64.b64decode(match.group(2), validate=True)
            except ValueError:
                raise AppError("invalid_input", "图片编码无效。") from None
            generated = [(image_bytes, match.group(1))]
            profile = {"model": "上传参考图"}
            prompt = extra.strip() or "用户上传参考图"
            ids = []
        else:
            if (
                kind == "character"
                and not subject["prompt"].strip()
                and not references
                and not extra.strip()
            ):
                raise AppError(
                    "invalid_input", "此角色尚无外观描述，请补充要求或上传参考图后生成。"
                )
            profile = service._load_config("image_profile")
            if not profile:
                raise AppError("image_not_configured", "请先配置生图模型。")
            key = resolve_key(profile.get("api_key_env", ""))
            if profile.get("backend") != "mflux" and not key:
                raise AppError("image_missing_api_key", "请先保存生图模型 API Key。")
            client = ImageGenerationClient(profile, key)
            generated = (
                client.generate(prompt, count, references=references)
                if references
                else client.generate(prompt, count)
            )
        metadata, written = [], []
        try:
            for image_bytes, mime in generated:
                width, height = image_dimensions(image_bytes, mime)
                if (
                    not 0 < len(image_bytes) <= MAX_IMAGE_BYTES
                    or min(width, height) <= 0
                    or width * height > 40_000_000
                ):
                    raise AppError("image_invalid_output", "生成图片大小或像素数超出限制。")
                asset_id = uuid4().hex
                filename = asset_id + {"image/png": ".png", "image/jpeg": ".jpg"}[mime]
                root = get_settings().data_dir / "assets" / "shots"
                root.mkdir(parents=True, exist_ok=True)
                path = root / filename
                path.write_bytes(image_bytes)
                written.append(path)
                metadata.append(
                    {
                        "asset_id": asset_id,
                        "filename": filename,
                        "mime_type": mime,
                        "width": width,
                        "height": height,
                        "prompt": prompt,
                        "model": str(profile["model"]),
                        "kind": kind,
                        "subject_id": subject_id,
                        "reference_assets": ids,
                        "created_at": now(),
                    }
                )
            service.save_shot_images(run_id, shot_id, source["source_hash"], metadata)
        except Exception:
            for path in written:
                path.unlink(missing_ok=True)
            raise
        return {"asset_ids": [image["asset_id"] for image in metadata]}
    if action == "prompts":
        category = body.get("category", "text")
        if category in {"image", "video"}:
            value = body.get("template")
            if not isinstance(value, str) or not value.strip() or len(value) > 20000:
                raise AppError("invalid_input", "请填写 1 到 20000 字的提示词。")
            templates = service._load_config("media_prompt_templates")
            templates[category] = value.strip()
            service._save_config("media_prompt_templates", templates)
            return {"saved": True}
        if category != "text":
            raise AppError("invalid_input", "提示词配置类型不正确。")
        templates = {}
        for stage in ("analysis", "outline", "shots", "review"):
            value = body.get(stage)
            if not isinstance(value, str) or not value.strip() or len(value) > 20000:
                raise AppError("invalid_input", "每个阶段请填写 1 到 20000 字的提示词。")
            templates[stage] = value.strip()
        service._save_config("prompt_templates", templates)
        return {"saved": True}
    if action == "work":
        title = str(body.get("title", "")).strip()
        if not title or len(title) > 200:
            raise AppError("invalid_input", "请填写 200 字以内的作品名称。")
        work_id = body.get("id")
        with service.sessions.begin() as session:
            if work_id:
                row = session.get(WorkRow, work_id)
                if row is None:
                    raise AppError("not_found", "未找到作品。")
                row.title = title
            else:
                work_id = uuid4().hex
                session.add(WorkRow(id=work_id, title=title))
            row = session.get(ConfigRow, "web_metadata")
            metadata = dict(row.value) if row else {}
            cover = str(body.get("cover", ""))
            if cover and not cover.startswith(
                ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
            ):
                raise AppError("invalid_input", "封面格式不支持。")
            if len(cover) > 2_900_000:
                raise AppError("invalid_input", "封面不能超过 2 MB。")
            metadata[work_id] = {
                "style": str(body.get("style", "国风动画"))[:100],
                "cover": cover,
                "updated": now(),
            }
            session.merge(ConfigRow(key="web_metadata", value=metadata))
        return {"id": work_id}
    if action == "chapter":
        ident = service.save_chapter(
            body["work_id"],
            body["title"],
            body["text"],
            body.get("context", ""),
            body.get("id"),
            body.get("revision"),
        )
        return {"id": ident}
    if action == "simple-profile":
        from novel_anime.integrations.language_models.client import LLMClient

        ident = body.get("id") or "online"
        existing: dict[str, object] = {
            key: value
            for key, value in service.load_profiles().get(ident, {}).items()
            if key in ModelProfile.model_fields
        }
        url = urlparse(str(body.get("url", "")))
        if (
            url.scheme != "https"
            or not url.netloc
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise AppError("invalid_input", "请填写不含账号、查询参数的 HTTPS API 地址。")
        timeout = float(body.get("timeout", 120))
        if not 10 <= timeout <= 600:
            raise AppError("invalid_input", "超时范围为 10 到 600 秒。")
        if "max_output" in body or "context_budget" in body:
            max_output = int(body.get("max_output", existing.get("max_output_tokens", 8000)))
            if not 256 <= max_output <= 128000:
                raise AppError("invalid_input", "最大输出范围为 256 到 128000 token。")
            context_budget = int(body.get("context_budget", existing.get("context_budget", 16000)))
            if context_budget <= max_output:
                raise AppError("invalid_input", f"上下文预算须大于最大输出 {max_output} token。")
        else:
            max_output = 0
            context_budget = 0
        profile = ModelProfile(
            **{
                **existing,
                "profile_id": ident,
                "provider": existing.get("provider", "线上模型"),
                "protocol": "chat_completions",
                "base_url": body["url"].rstrip("/"),
                "model": body.get("model", "catalog"),
                "timeout": timeout,
                "temperature": body.get("temperature", 1),
                "max_output_tokens": max_output,
                "context_budget": context_budget,
                "enabled": body.get("enabled", True),
                "api_key_env": existing.get("api_key_env") or f"YINGZHANG_KEY_{ident}",
            }
        )
        supplied = body.get("key", "")
        if not isinstance(supplied, str) or len(supplied) > 4096 or "\n" in supplied:
            raise AppError("invalid_input", "API Key 格式不正确。")
        if body.get("list_models"):
            # Transient key used only for this catalog request; never persisted by listing.
            models = LLMClient(profile, api_key=supplied or None).list_models()
            return {"models": models}
        service.save_profile(profile.model_dump())
        if body.get("clear_key"):
            store_key(profile.api_key_env, "")
        elif supplied.strip():
            store_key(profile.api_key_env, supplied)
        return {"id": profile.profile_id}
    if action == "image-profile":
        name = str(body.get("name", "")).strip()
        backend = str(body.get("backend", "openai"))
        base_url = str(body.get("base_url", "")).strip().rstrip("/")
        model = str(body.get("model", "")).strip()
        executable = str(body.get("executable", "")).strip()
        base_model = str(body.get("base_model", "flux2-klein-4b")).strip()
        api_key_env = str(body.get("api_key_env", "YINGZHANG_IMAGE_API_KEY")).strip()
        timeout = float(body.get("timeout", 300))
        parsed = urlparse(base_url)
        if (
            not name
            or len(name) > 100
            or not model
            or len(model) > 200
            or backend not in {"openai", "mflux"}
            or (
                backend == "openai"
                and (
                    parsed.scheme not in {"https", "http"}
                    or not parsed.netloc
                    or parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                )
            )
            or (
                backend == "mflux"
                and (
                    not executable
                    or len(executable) > 1000
                    or not base_model
                    or len(base_model) > 200
                )
            )
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env)
            or not 10 <= timeout <= 1800
        ):
            raise AppError(
                "invalid_input", "请检查生图模型名称、本地运行器或 API 地址，以及超时设置。"
            )
        key = body.get("api_key")
        if (
            not isinstance(key, str)
            or len(key) > 4096
            or "\n" in key
            or (backend == "openai" and not key.strip() and not resolve_key(api_key_env))
        ):
            raise AppError("invalid_input", "API Key 格式不正确。")
        service._save_config(
            "image_profile",
            {
                "name": name,
                "backend": backend,
                "base_url": base_url,
                "model": model,
                "executable": executable,
                "base_model": base_model,
                "api_key_env": api_key_env,
                "timeout": timeout,
            },
        )
        if body.get("clear_key"):
            store_key(api_key_env, "")
        elif key.strip():
            store_key(api_key_env, key)
        return {"saved": True}
    if action == "profile":
        profile = ModelProfile(**body)
        service.save_profile(profile.model_dump())
        return {"id": profile.profile_id}
    if action == "run":
        profiles = service.load_profiles()
        selected = profiles.get(str(body.get("profile_id") or ""))
        if not selected:
            raise AppError("invalid_input", "请先保存并选择模型配置。")
        if not selected.get("enabled", True):
            raise AppError("model_disabled", "模型已关闭，请在设置中启用。")
        if not selected.get("api_key_env") or not resolve_key(selected["api_key_env"]):
            raise AppError("missing_api_key", "请在模型配置中填写 API Key 并保存设置。")
        ident = service.create_run(
            body["chapter_id"],
            selected,
            body.get("style", "国风动画"),
            body.get("aspect_ratio", "16:9"),
            body.get("preference", ""),
            body["idempotency_key"],
            target_stage=body.get("stage", "analysis"),
            source_run_id=body.get("source_run_id"),
            source_revision=body.get("source_revision"),
            selected_stages=body.get("selected_stages"),
        )
        return {"id": ident}
    if action == "adopt-characters":
        count = service.adopt_character_suggestions(body["work_id"])
        return {"adopted": count}
    if action == "character-image":
        work_id = str(body.get("work_id", ""))
        character_id = str(body.get("character_id", ""))
        if not re.fullmatch(r"[a-f0-9]{32}", work_id) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]*", character_id
        ):
            raise AppError("invalid_input", "作品或角色标识格式不正确。")
        profiles = {item["character_id"]: item for item in service.list_visual_profiles(work_id)}
        profile = profiles.get(character_id)
        if profile is None:
            raise AppError("not_found", "请先保存该角色的视觉资料。")
        match = IMAGE_DATA_URL.fullmatch(str(body.get("data_url", "")))
        if not match:
            raise AppError("invalid_input", "仅支持 PNG 或 JPEG 图片。")
        mime_type, encoded = match.groups()
        if len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 8:
            raise AppError("invalid_input", "图片不能超过 8 MB。")
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
            width, height = image_dimensions(image_bytes, mime_type)
        except Exception as exc:
            raise AppError("invalid_input", "图片文件损坏或格式与内容不一致。") from exc
        if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES or width * height > 40_000_000:
            raise AppError("invalid_input", "图片大小或像素数超出限制。")
        costume_version = str(body.get("costume_version") or profile["costume_version"])
        if costume_version != profile["costume_version"]:
            raise AppError("invalid_input", "请先保存目标服装版本，再上传对应参考图。")
        asset_id = uuid4().hex
        extension = {"image/png": ".png", "image/jpeg": ".jpg"}[mime_type]
        asset_parent = (get_settings().data_dir / "assets" / "characters").resolve()
        asset_root = (asset_parent / work_id / character_id).resolve()
        if asset_parent not in asset_root.parents:
            raise AppError("invalid_input", "素材路径不合法。")
        asset_root.mkdir(parents=True, exist_ok=True)
        target = (asset_root / f"{asset_id}{extension}").resolve()
        if asset_root not in target.parents:
            raise AppError("invalid_input", "素材路径不合法。")
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(image_bytes)
        temporary.replace(target)
        try:
            service.add_character_image(
                work_id,
                {
                    "asset_id": asset_id,
                    "work_id": work_id,
                    "character_id": character_id,
                    "costume_version": costume_version,
                    "purpose": "candidate",
                    "filename": f"{asset_id}{extension}",
                    "mime_type": mime_type,
                    "size_bytes": len(image_bytes),
                    "width": width,
                    "height": height,
                    "sha256": hashlib.sha256(image_bytes).hexdigest(),
                    "profile_version": profile["version"],
                    "created_at": now(),
                },
            )
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return {"asset_id": asset_id}
    if action == "adopt-character-image":
        version = service.adopt_character_image(
            str(body["work_id"]),
            str(body["asset_id"]),
            str(body["purpose"]),
            int(body["profile_version"]),
        )
        return {"version": version}
    if action == "delete-character-image":
        work_id = str(body["work_id"])
        character_id = str(body["character_id"])
        asset_id = str(body["asset_id"])
        if (
            not re.fullmatch(r"[a-f0-9]{32}", work_id)
            or not re.fullmatch(r"[a-f0-9]{32}", asset_id)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", character_id)
        ):
            raise AppError("invalid_input", "素材标识格式不正确。")
        service.delete_character_image(work_id, asset_id)
        asset_root = get_settings().data_dir / "assets" / "characters"
        for extension in (".png", ".jpg"):
            path = (asset_root / work_id / character_id / f"{asset_id}{extension}").resolve()
            if asset_root.resolve() in path.parents and path.is_file():
                path.unlink()
        return {"deleted": True}
    if action == "image-config":
        image_profile = service._load_config("image_profile")
        configured = bool(
            image_profile
            and (
                image_profile.get("backend") == "mflux"
                or resolve_key(image_profile.get("api_key_env", ""))
            )
        )
        return {
            "configured": configured,
            "message": "生图模型已配置。" if configured else "尚未配置生图模型。",
        }
    if action == "generate-character-image":
        profile = service._load_config("image_profile")
        if not profile:
            raise AppError("image_not_configured", "请先在模型配置中保存生图模型。")
        api_key = resolve_key(profile.get("api_key_env", ""))
        if profile.get("backend") != "mflux" and not api_key:
            raise AppError("image_missing_api_key", "请在模型配置中保存生图模型 API Key。")
        work_id = str(body.get("work_id", ""))
        character_id = str(body.get("character_id", ""))
        character = next(
            (
                item
                for item in service.list_visual_profiles(work_id)
                if item["character_id"] == character_id
            ),
            None,
        )
        if character is None:
            raise AppError("not_found", "请先保存该角色的视觉资料。")
        prompt = str(body.get("prompt", "")).strip()
        if not prompt or len(prompt) > 10000:
            raise AppError("invalid_input", "生图提示词需为 1 到 10000 字。")
        count = int(body.get("count", 1))
        if not 1 <= count <= 4:
            raise AppError("invalid_input", "单次生成数量需为 1 到 4 张。")
        from novel_anime.integrations.image_generation.client import ImageGenerationClient

        generated = ImageGenerationClient(profile, api_key).generate(prompt, count)
        asset_ids = []
        for image_bytes, mime_type in generated:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            result = dispatch(
                service,
                "character-image",
                {
                    "work_id": work_id,
                    "character_id": character_id,
                    "costume_version": character["costume_version"],
                    "data_url": f"data:{mime_type};base64,{encoded}",
                },
            )
            asset_ids.append(result["asset_id"])
        return {"asset_ids": asset_ids}
    if action in {"pause", "resume", "retry", "stop"}:
        if action in {"pause", "stop"}:
            getattr(service, action + "_run")(body["id"])
        else:
            getattr(service, action + "_run")(body["id"], stage_only=True)
        return {"id": body["id"]}
    raise AppError("invalid_input", "不支持的操作。")


def image_dimensions(image_bytes: bytes, mime_type: str) -> tuple[int, int]:
    """Parse dimensions only after checking the image container header and segments."""
    if mime_type == "image/png":
        if len(image_bytes) < 45 or image_bytes[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("invalid PNG")
        import zlib

        index = 8
        dimensions = None
        ended = False
        has_image_data = False
        while index + 12 <= len(image_bytes):
            size = int.from_bytes(image_bytes[index : index + 4], "big")
            end = index + 12 + size
            if end > len(image_bytes):
                raise ValueError("truncated PNG")
            kind = image_bytes[index + 4 : index + 8]
            data = image_bytes[index + 8 : index + 8 + size]
            crc = int.from_bytes(image_bytes[index + 8 + size : end], "big")
            if zlib.crc32(kind + data) & 0xFFFFFFFF != crc:
                raise ValueError("invalid PNG checksum")
            if kind == b"IHDR":
                if size != 13 or dimensions is not None:
                    raise ValueError("invalid PNG header")
                dimensions = struct.unpack(">II", data[:8])
            elif kind == b"IDAT" and dimensions:
                if len(data) < 2:
                    raise ValueError("invalid PNG data")
                has_image_data = True
            elif kind == b"IEND":
                if size != 0 or end != len(image_bytes):
                    raise ValueError("invalid PNG end")
                ended = True
                break
            index = end
        if dimensions and ended and has_image_data:
            return dimensions
        raise ValueError("incomplete PNG")
    if (
        mime_type != "image/jpeg"
        or not image_bytes.startswith(b"\xff\xd8")
        or not image_bytes.endswith(b"\xff\xd9")
    ):
        raise ValueError("invalid JPEG")
    index = 2
    while index + 4 < len(image_bytes) - 2:
        if image_bytes[index] != 0xFF:
            index += 1
            continue
        marker = image_bytes[index + 1]
        index += 2
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        segment_length = int.from_bytes(image_bytes[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(image_bytes):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if segment_length < 7:
                break
            height, width = struct.unpack(">HH", image_bytes[index + 3 : index + 7])
            return width, height
        index += segment_length
    raise ValueError("invalid JPEG")


class Handler(BaseHTTPRequestHandler):
    def send(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(data)

    def json(self, status: int, value: dict | list) -> None:
        profiles = StudioService().load_profiles()
        secrets = tuple(resolve_key(p.get("api_key_env", "")) for p in profiles.values())
        self.send(
            status,
            json.dumps(
                safe_payload(value, tuple(s for s in secrets if s)), ensure_ascii=False
            ).encode(),
            "application/json; charset=utf-8",
        )

    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            service = StudioService()
            if path == "/api/state":
                self.json(200, snapshot(service))
            elif path.startswith("/api/runs/"):
                self.json(200, service.list_runs(path.removeprefix("/api/runs/")))
            elif path.startswith("/api/overview/"):
                self.json(200, service.chapter_overview(path.removeprefix("/api/overview/")))
            elif path.startswith("/assets/shots/"):
                from sqlalchemy import select

                from novel_anime.persistence.database import ShotImageRow

                asset_id = Path(path).name.removesuffix(".img")
                with service.sessions() as session:
                    row = session.scalar(
                        select(ShotImageRow).where(ShotImageRow.asset_id == asset_id)
                    )
                    filename = row.filename if row else ""
                    mime = row.mime_type if row else ""
                root = (get_settings().data_dir / "assets" / "shots").resolve()
                target = (root / filename).resolve()
                if not filename or root not in target.parents or not target.is_file():
                    self.send(404, b"Not found", "text/plain")
                    return
                self.send(200, target.read_bytes(), mime)
            elif path.startswith("/assets/characters/"):
                asset_root = (get_settings().data_dir / "assets").resolve()
                asset_id = Path(path).name.removesuffix(".img")
                if not re.fullmatch(r"[a-f0-9]{32}", asset_id):
                    self.send(404, b"Not found", "text/plain")
                    return
                row = next(
                    (
                        item
                        for work in service.list_works()
                        for item in service.list_character_images(work["id"])
                        if item["asset_id"] == asset_id
                    ),
                    None,
                )
                if row is None:
                    self.send(404, b"Not found", "text/plain")
                    return
                target = (
                    asset_root
                    / "characters"
                    / row["work_id"]
                    / row["character_id"]
                    / row["filename"]
                ).resolve()
                if asset_root not in target.parents or not target.is_file():
                    self.send(404, b"Not found", "text/plain")
                    return
                mime = {".png": "image/png", ".jpg": "image/jpeg"}.get(target.suffix)
                if not mime:
                    self.send(404, b"Not found", "text/plain")
                    return
                self.send(200, target.read_bytes(), mime)
            elif path == "/health":
                self.send(200, b"ok", "text/plain")
            elif path in {"/", "/index.html", "/cloud.js"}:
                name = "cloud.js" if path == "/cloud.js" else "index.html"
                mime = "text/javascript" if name.endswith(".js") else "text/html"
                self.send(200, (STATIC / name).read_bytes(), mime + "; charset=utf-8")
            else:
                self.send(404, b"Not found", "text/plain")
        except Exception:
            LOG.exception("Web read failed")
            self.send(500, b'{"error":"Unable to load data"}', "application/json")

    def do_POST(self) -> None:
        # No CORS; block browser cross-site writes to a loopback/SSH-only service.
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self.send(403, b'{"error":"Origin rejected"}', "application/json")
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.send(415, b"JSON required", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 12_000_000:
                self.send(413, b"Request too large", "text/plain")
                return
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError()
            self.json(
                200, dispatch(StudioService(), urlparse(self.path).path.removeprefix("/api/"), body)
            )
        except AppError as exc:
            self.json(
                409 if exc.code == "version_conflict" else 400,
                {"error": exc.message, "code": exc.code},
            )
        except (ValueError, KeyError, TypeError):
            self.json(400, {"error": "请求字段缺失或格式不正确。"})
        except Exception:
            LOG.exception("Web write failed")
            self.json(500, {"error": "保存失败，请刷新核对后再试。"})


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    host = os.environ.get("YINGZHANG_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("YINGZHANG_WEB_PORT", "8501"))
    server = ThreadingHTTPServer((host, port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
