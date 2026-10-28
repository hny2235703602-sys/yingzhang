"""Manual image creation with independent storage and explicit references."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from novel_anime.application.service import StudioService, as_dict
from novel_anime.core.credentials import resolve_key
from novel_anime.core.errors import AppError
from novel_anime.core.settings import get_settings
from novel_anime.integrations.image_generation.client import ImageGenerationClient
from novel_anime.persistence.database import StudioImageRow

MAX_IMAGE_BYTES = 8_000_000
MAX_GENERATED_IMAGES = 8


def prune_images(service: StudioService) -> None:
    """Only studio generation history expires; library copies and uploads are independent."""
    root = (get_settings().data_dir / "assets" / "studio").resolve()
    expired = []
    with service.sessions.begin() as session:
        rows = list(session.scalars(
            select(StudioImageRow)
            .where(StudioImageRow.purpose == "generated")
            .order_by(StudioImageRow.created_at.desc(), StudioImageRow.asset_id.desc())
        ))
        for row in rows[MAX_GENERATED_IMAGES:]:
            path = (root / row.filename).resolve()
            if root in path.parents:
                expired.append(path)
            session.delete(row)
    for path in expired:
        path.unlink(missing_ok=True)


def list_images(service: StudioService) -> list[dict[str, Any]]:
    prune_images(service)
    with service.sessions() as session:
        return [
            as_dict(row)
            for row in session.scalars(
                select(StudioImageRow).order_by(
                    StudioImageRow.created_at.desc(), StudioImageRow.asset_id.desc()
                )
            )
        ]


def image_asset(service: StudioService, asset_id: str) -> tuple[Path, str]:
    if not re.fullmatch(r"[a-f0-9]{32}", asset_id):
        raise AppError("not_found", "图片不存在。")
    with service.sessions() as session:
        row = session.get(StudioImageRow, asset_id)
        if row is None:
            raise AppError("not_found", "图片不存在。")
        root = (get_settings().data_dir / "assets" / "studio").resolve()
        path = (root / row.filename).resolve()
        if root not in path.parents or not path.is_file():
            raise AppError("not_found", "图片文件不存在，请重新上传。")
        return path, row.mime_type


def keep_reference(service: StudioService, asset_id: str) -> dict:
    """Selected references have their own copy, outside the expiring results list."""
    path, mime = image_asset(service, asset_id)
    return create_images(service, {
        "data_url": f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
    }, upload=True)


def create_images(service: StudioService, body: dict, *, upload: bool = False) -> dict:
    from novel_anime.ui.web_server import image_dimensions

    positive, negative = body.get("positive_prompt", ""), body.get("negative_prompt", "")
    count = body.get("count", 1)
    if (
        not isinstance(positive, str)
        or len(positive) > 20000
        or not isinstance(negative, str)
        or len(negative) > 10000
        or type(count) is not int
        or not 1 <= count <= 4
        or (not upload and not positive.strip())
    ):
        raise AppError(
            "invalid_input",
            "请填写正向提示词（最多 20000 字），反向最多 10000 字，生成 1–4 张。"
        )
    positive, negative = positive.strip(), negative.strip()
    ids = body.get("reference_assets", [])
    if (
        not isinstance(ids, list)
        or len(ids) > 6
        or any(not isinstance(item, str) for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise AppError("invalid_input", "最多选择 6 张不同的参考图。")
    prompt = positive
    if negative:
        prompt += "\n\n【反向提示词 · 请避免以下内容】\n" + negative
    if upload:
        match = re.fullmatch(
            r"data:(image/(?:png|jpeg));base64,([A-Za-z0-9+/=]+)",
            str(body.get("data_url", "")),
        )
        if not match or len(match[2]) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
            raise AppError("invalid_input", "请上传 8 MB 以内的 PNG、JPG 或 JPEG 图片。")
        try:
            content = base64.b64decode(match[2], validate=True)
        except ValueError:
            raise AppError("invalid_input", "图片编码无效。") from None
        generated = [(content, match[1])]
        positive, negative, prompt, model, ids = "", "", "用户上传参考图", "上传参考图", []
    else:
        references = []
        for ident in ids:
            path, mime = image_asset(service, ident)
            references.append((path.read_bytes(), mime))
        for mention in re.findall(r"图\s*([一二三四五六七八九十]|\d+)", positive):
            number = (
                int(mention)
                if mention.isdigit()
                else "一二三四五六七八九十".index(mention) + 1
            )
            if not 1 <= number <= len(ids):
                raise AppError("invalid_input", "正向提示词引用了未选中的参考图，请核对编号。")
        if ids:
            prompt += "\n\n【参考图编号】\n" + "\n".join(
                f"图{'一二三四五六'[index]}（图{index + 1}）对应本次输入的第{index + 1}张图片。"
                for index in range(len(ids))
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
        model = str(profile["model"])
    root = get_settings().data_dir / "assets" / "studio"
    written: list[Path] = []
    records = []
    try:
        for content, mime in generated:
            if not 0 < len(content) <= MAX_IMAGE_BYTES:
                raise AppError("image_invalid_output", "图片超出大小限制。")
            try:
                width, height = image_dimensions(content, mime)
            except (ValueError, KeyError):
                raise AppError("image_invalid_output", "图片格式无效。") from None
            if min(width, height) <= 0 or width * height > 40_000_000:
                raise AppError("image_invalid_output", "图片像素数超出限制。")
            asset_id = uuid4().hex
            filename = asset_id + {"image/png": ".png", "image/jpeg": ".jpg"}[mime]
            root.mkdir(parents=True, exist_ok=True)
            path = root / filename
            path.write_bytes(content)
            written.append(path)
            records.append(
                StudioImageRow(
                    asset_id=asset_id,
                    filename=filename,
                    mime_type=mime,
                    width=width,
                    height=height,
                    purpose="reference" if upload else "generated",
                    positive_prompt=positive,
                    negative_prompt=negative,
                    prompt=prompt,
                    model=model,
                    reference_assets=ids,
                )
            )
        with service.sessions.begin() as session:
            session.add_all(records)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    prune_images(service)
    return {"asset_ids": [path.stem for path in written]}
