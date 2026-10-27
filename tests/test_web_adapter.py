from pathlib import Path
import base64
import struct
import zlib

import pytest
from alembic import command
from alembic.config import Config

from novel_anime.application.service import StudioService
from novel_anime.core.errors import AppError
from novel_anime.core.settings import ROOT
from novel_anime.ui.web_server import dispatch, snapshot


def test_web_adapter_persists_and_checks_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("YINGZHANG_DATA_DIR", str(tmp_path))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")
    service = StudioService()
    work = dispatch(service, "work", {"title": "云端验证", "style": "二维动画"})["id"]
    chapter = dispatch(
        service, "chapter", {"work_id": work, "title": "第一章", "text": "少年走进庭院。"}
    )["id"]
    state = snapshot(StudioService())
    assert state["novels"][0]["chapters"][0]["id"] == chapter
    assert state["novels"][0]["style"] == "二维动画"
    dispatch(
        service,
        "chapter",
        {
            "work_id": work,
            "id": chapter,
            "revision": 1,
            "title": "第一章",
            "text": "少年走进庭院，停下脚步。",
        },
    )
    with pytest.raises(AppError, match="章节已更新"):
        dispatch(
            service,
            "chapter",
            {"work_id": work, "id": chapter, "revision": 1, "title": "第一章", "text": "不应覆盖"},
        )
    assert service.get_chapter(chapter)["revision"] == 2
    with pytest.raises(AppError, match="请先保存"):
        dispatch(service, "run", {"chapter_id": chapter, "profile_id": "missing"})


def test_simple_profile_secret_keep_clear_and_disabled(monkeypatch, tmp_path):
    import json

    from novel_anime.core.credentials import credential_path, resolve_key

    monkeypatch.setenv("YINGZHANG_DATA_DIR", str(tmp_path))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")
    service = StudioService()
    body = {
        "id": "online",
        "url": "https://example.com/v1",
        "model": "test",
        "timeout": 120,
        "temperature": 1,
        "enabled": True,
        "key": "test-private-value",
    }
    dispatch(service, "simple-profile", body)
    assert service.load_profiles()["online"]["max_output_tokens"] == 0
    assert service.load_profiles()["online"]["context_budget"] == 0
    env = service.load_profiles()["online"]["api_key_env"]
    assert resolve_key(env) == "test-private-value"
    assert credential_path().stat().st_mode & 0o777 == 0o600
    assert "test-private-value" not in json.dumps(snapshot(service))
    assert snapshot(service)["profiles"][0]["key_configured"] is True
    assert snapshot(service)["profiles"][0]["key_tail"] == "alue"
    dispatch(service, "simple-profile", {**body, "key": "", "temperature": 0.7})
    assert resolve_key(env) == "test-private-value"
    assert service.load_profiles()["online"]["temperature"] == 0.7
    dispatch(service, "simple-profile", {**body, "key": "", "enabled": False})
    with pytest.raises(AppError, match="模型已关闭"):
        dispatch(service, "run", {"profile_id": "online"})
    monkeypatch.setenv(env, "legacy-env-key")
    dispatch(service, "simple-profile", {**body, "key": "", "clear_key": True})
    assert resolve_key(env) == ""
    assert snapshot(service)["profiles"][0]["key_configured"] is False
    assert snapshot(service)["profiles"][0]["key_tail"] == ""


def _png_1x1() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


def test_character_image_upload_adopt_and_serve(monkeypatch, tmp_path):
    monkeypatch.setenv("YINGZHANG_DATA_DIR", str(tmp_path))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")
    service = StudioService()
    work = service.create_work("角色素材")
    service.save_visual_profile(
        work,
        {
            "character_id": "hero",
            "name": "少年",
            "appearance": "黑发",
            "costume": "蓝袍",
            "costume_version": "costume_01",
            "version": 1,
        },
    )
    payload = "data:image/png;base64," + base64.b64encode(_png_1x1()).decode("ascii")
    result = dispatch(
        service,
        "character-image",
        {"work_id": work, "character_id": "hero", "data_url": payload},
    )
    asset = service.list_character_images(work, "hero")[0]
    assert asset["asset_id"] == result["asset_id"]
    stored = tmp_path / "assets" / "characters" / work / "hero" / asset["filename"]
    assert stored.read_bytes() == _png_1x1()
    dispatch(
        service,
        "adopt-character-image",
        {"work_id": work, "asset_id": result["asset_id"], "purpose": "primary", "profile_version": 1},
    )
    assert service.list_visual_profiles(work)[0]["reference_assets"] == [result["asset_id"]]
    with pytest.raises(AppError, match="损坏或格式"):
        dispatch(
            service,
            "character-image",
            {
                "work_id": work,
                "character_id": "hero",
                "data_url": "data:image/png;base64," + base64.b64encode(b"not an image").decode(),
            },
        )


def test_image_generation_is_explicitly_unconfigured(service):
    result = dispatch(service, "image-config", {})
    assert result["configured"] is False
    assert "尚未配置" in result["message"]


def test_image_profile_key_is_stored_outside_profile_and_snapshot(service, monkeypatch):
    from novel_anime.core.credentials import resolve_key

    result = dispatch(
        service,
        "image-profile",
        {
            "name": "绘图服务",
            "base_url": "https://images.example/v1",
            "model": "illustrator",
            "api_key_env": "YINGZHANG_IMAGE_API_KEY",
            "api_key": "image-secret",
            "timeout": 90,
        },
    )
    assert result["saved"] is True
    assert service._load_config("image_profile")["model"] == "illustrator"
    assert resolve_key("YINGZHANG_IMAGE_API_KEY") == "image-secret"
    assert "image-secret" not in str(snapshot(service))
    assert snapshot(service)["image_profile"]["key_tail"] == "cret"
    assert dispatch(service, "image-config", {})["configured"] is True
    dispatch(
        service,
        "image-profile",
        {
            "name": "绘图服务",
            "base_url": "https://images.example/v1",
            "model": "illustrator",
            "api_key_env": "YINGZHANG_IMAGE_API_KEY",
            "api_key": "",
            "timeout": 90,
            "clear_key": True,
        },
    )
    assert resolve_key("YINGZHANG_IMAGE_API_KEY") == ""


def test_generate_character_image_saves_model_output_as_candidate(service, monkeypatch):
    from novel_anime.integrations.image_generation.client import ImageGenerationClient

    work = service.create_work("生成验证")
    service.save_visual_profile(
        work,
        {
            "character_id": "hero",
            "name": "少年",
            "appearance": "黑发",
            "costume": "蓝袍",
            "costume_version": "costume_01",
            "version": 1,
        },
    )
    dispatch(
        service,
        "image-profile",
        {
            "name": "绘图服务",
            "base_url": "https://images.example/v1",
            "model": "illustrator",
            "api_key": "image-secret",
            "timeout": 90,
        },
    )
    monkeypatch.setattr(ImageGenerationClient, "generate", lambda self, prompt, count: [(_png_1x1(), "image/png")])

    result = dispatch(
        service,
        "generate-character-image",
        {"work_id": work, "character_id": "hero", "prompt": "角色参考图", "count": 1},
    )

    assert len(result["asset_ids"]) == 1
    assets = service.list_character_images(work, "hero")
    assert len(assets) == 1
    assert assets[0]["asset_id"] == result["asset_ids"][0]
    assert assets[0]["purpose"] == "candidate"
