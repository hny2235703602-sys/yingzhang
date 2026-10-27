import base64
import struct
import zlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from novel_anime.application.service import StudioService
from novel_anime.core.errors import AppError
from novel_anime.core.settings import ROOT
from novel_anime.ui.web_server import dispatch, snapshot


@pytest.fixture
def service(monkeypatch, tmp_path):
    monkeypatch.setenv("YINGZHANG_DATA_DIR", str(tmp_path))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")
    return StudioService()


def test_prompt_categories_save_independently(service):
    defaults = snapshot(service)
    assert "图片风格" in defaults["media_templates"]["image"]
    dispatch(
        service, "prompts", {"category": "image", "template": "角色：{name}，外貌：{appearance}"}
    )
    dispatch(service, "prompts", {"category": "video", "template": "保持角色连续性"})
    text = {stage: "文本提示词 " + stage for stage in ("analysis", "outline", "shots", "review")}
    dispatch(service, "prompts", {"category": "text", **text})
    state = snapshot(StudioService())
    assert state["templates"] == text
    assert state["media_templates"] == {
        "image": "角色：{name}，外貌：{appearance}",
        "video": "保持角色连续性",
    }
    with pytest.raises(AppError):
        dispatch(service, "prompts", {"category": "image", "template": " "})
    assert snapshot(service)["media_templates"] == state["media_templates"]


def test_overview_shows_chapter_shots_and_only_adopted_character_references(service):
    from novel_anime.domain.models import ModelProfile
    from novel_anime.persistence.database import RunRow

    work = service.create_work("总览作品")
    chapter = service.save_chapter(work, "第一章", "少年推门。", "")
    other = service.save_chapter(work, "第二章", "少女走来。", "")
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
    adopted = dispatch(
        service, "character-image", {"work_id": work, "character_id": "hero", "data_url": payload}
    )["asset_id"]
    dispatch(
        service,
        "adopt-character-image",
        {"work_id": work, "asset_id": adopted, "purpose": "primary", "profile_version": 1},
    )
    dispatch(
        service, "character-image", {"work_id": work, "character_id": "hero", "data_url": payload}
    )
    profile = ModelProfile(
        provider="test",
        protocol="chat_completions",
        base_url="https://example.com/v1",
        model="test",
    ).model_dump()
    run = service.create_run(chapter, profile, "二维动画", "16:9", "", "overview-old")
    with service.sessions.begin() as session:
        row = session.get(RunRow, run)
        row.result = {
            "shots": [
                {"shot_id": "s2", "order": 2, "character_ids": []},
                {
                    "shot_id": "s1",
                    "order": 1,
                    "character_ids": ["hero"],
                    "character_prompts": [{"character_id": "hero", "prompt": "黑色长发，蓝色长袍"}],
                },
            ]
        }
        row.status = "review_required"
    service.create_run(chapter, profile, "二维动画", "16:9", "", "overview-pending")
    result = service.chapter_overview(chapter)
    assert result["run"]["id"] == run
    assert [shot["shot_id"] for shot in result["shots"]] == ["s1", "s2"]
    assert result["shots"][0]["reference_images"] == [
        {"asset_id": adopted, "character_name": "少年"}
    ]
    assert result["shots"][1]["reference_images"] == []
    assert result["shots"][0]["subjects"][0]["name"] == "少年"
    assert result["shots"][0]["subjects"][0]["description"] == "黑色长发，蓝色长袍"
    assert result["shots"][0]["image_source"]["character_prompt"] == "黑色长发，蓝色长袍"
    assert result["shots"][0]["subjects"][0]["images"] == [{"asset_id": adopted}]
    assert result["shots"][1]["subjects"] == []
    assert service.chapter_overview(other) == {"shots": [], "run": None}
    with pytest.raises(AppError):
        service.chapter_overview("missing")


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
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

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
        {
            "work_id": work,
            "asset_id": result["asset_id"],
            "purpose": "primary",
            "profile_version": 1,
        },
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
    monkeypatch.setattr(
        ImageGenerationClient, "generate", lambda self, prompt, count: [(_png_1x1(), "image/png")]
    )

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


@pytest.fixture
def shot_studio(service):
    from novel_anime.domain.models import ModelProfile
    from novel_anime.persistence.database import RunRow

    work = service.create_work("镜头绘制作品")
    chapter = service.save_chapter(work, "第一章", "少年站在庭院。", "")
    profile = ModelProfile(
        provider="test",
        protocol="chat_completions",
        base_url="https://example.com/v1",
        model="test",
    ).model_dump()
    run_id = service.create_run(chapter, profile, "二维动画", "16:9", "", "shot-studio")
    shot = {
        "shot_id": "s1",
        "scene_id": "scene1",
        "order": 1,
        "title": "庭院少年",
        "character_ids": ["hero"],
        "character_prompt": "黑发少年，蓝色长袍",
        "environment_prompt": "清晨庭院，石板路，竹影",
        "composition": "人物在画面左侧",
        "action": "少年抬头",
    }
    with service.sessions.begin() as session:
        row = session.get(RunRow, run_id)
        row.result = {
            "shots": [shot],
            "analysis": {
                "characters": [{"character_id": "hero", "name": "少年", "description": "黑发"}]
            },
            "outline": {
                "scenes": [
                    {
                        "scene_id": "scene1",
                        "location": "庭院",
                        "time": "清晨",
                        "script": "少年站在庭院。",
                    }
                ]
            },
        }
        row.status = "review_required"
    dispatch(
        service,
        "image-profile",
        {
            "name": "测试生图",
            "model": "test-image",
            "base_url": "https://example.com/v1",
            "api_key": "secret-image-key",
            "timeout": 90,
        },
    )
    dispatch(service, "prompts", {"category": "image", "template": "水彩风格，用于影片分镜预览"})
    return chapter, run_id


def test_shot_generation_combines_sources_persists_and_appears_in_overview(
    service, shot_studio, monkeypatch, tmp_path
):
    from novel_anime.integrations.image_generation.client import ImageGenerationClient

    chapter, run_id = shot_studio
    captured = {}

    def generate(client, prompt, count):
        captured.update(prompt=prompt, count=count, key=client.api_key)
        return [(_png_1x1(), "image/png")] * count

    monkeypatch.setattr(ImageGenerationClient, "generate", generate)
    source = service.shot_image_source(run_id, "s1")
    result = dispatch(
        service,
        "generate-shot-image",
        {
            "run_id": run_id,
            "shot_id": "s1",
            "source_hash": source["source_hash"],
            "global_prompt": "水彩风格，用于影片分镜预览",
            "extra": "柔和晨光",
            "count": 2,
        },
    )
    assert captured["count"] == 2
    assert captured["key"] == "secret-image-key"
    for fragment in (
        "水彩风格",
        "影片分镜预览",
        "黑发少年",
        "清晨庭院",
        "人物在画面左侧",
        "柔和晨光",
    ):
        assert fragment in captured["prompt"]
    saved = StudioService().shot_image_source(run_id, "s1")["images"]
    assert len(saved) == 2
    assert all(not item["selected"] for item in saved)
    assert all(item["prompt"] == captured["prompt"] for item in saved)
    assert "secret-image-key" not in str(saved)
    assert (tmp_path / "assets" / "shots" / saved[0]["filename"]).read_bytes() == _png_1x1()
    for asset_id in result["asset_ids"]:
        dispatch(
            service, "select-shot-image", {"run_id": run_id, "shot_id": "s1", "asset_id": asset_id}
        )
    images = service.chapter_overview(chapter)["shots"][0]["image_source"]["images"]
    assert [image["asset_id"] for image in images if image["selected"]] == [result["asset_ids"][-1]]


def test_shot_generation_rejects_stale_source_or_global_config_before_calling_model(
    service, shot_studio, monkeypatch
):
    from novel_anime.integrations.image_generation.client import ImageGenerationClient

    _, run_id = shot_studio
    monkeypatch.setattr(
        ImageGenerationClient,
        "generate",
        lambda *args: pytest.fail("stale request must not call model"),
    )
    source = service.shot_image_source(run_id, "s1")
    body = {
        "run_id": run_id,
        "shot_id": "s1",
        "source_hash": source["source_hash"],
        "global_prompt": "水彩风格，用于影片分镜预览",
    }
    for changes in (
        {"source_hash": "stale"},
        {"global_prompt": "旧风格"},
        {"count": 5},
        {"extra": "x" * 2001},
    ):
        with pytest.raises(AppError):
            dispatch(service, "generate-shot-image", {**body, **changes})
    with pytest.raises(AppError):
        dispatch(
            service, "select-shot-image", {"run_id": run_id, "shot_id": "s1", "asset_id": "missing"}
        )


def test_shot_images_are_versioned_and_missing_appearance_never_uses_biography(
    service, shot_studio, monkeypatch
):
    from novel_anime.integrations.image_generation.client import ImageGenerationClient
    from novel_anime.persistence.database import RunRow

    _, run_id = shot_studio
    source = service.shot_image_source(run_id, "s1")
    monkeypatch.setattr(
        ImageGenerationClient, "generate", lambda *args: [(_png_1x1(), "image/png")]
    )
    result = dispatch(
        service,
        "generate-shot-image",
        {
            "run_id": run_id,
            "shot_id": "s1",
            "source_hash": source["source_hash"],
            "global_prompt": "水彩风格，用于影片分镜预览",
        },
    )
    with service.sessions.begin() as session:
        row = session.get(RunRow, run_id)
        result_data = dict(row.result)
        shot = {**result_data["shots"][0], "character_prompt": "", "environment_prompt": ""}
        result_data["shots"] = [shot]
        row.result = result_data
    updated = service.shot_image_source(run_id, "s1")
    assert "请重新生成分镜" in updated["character_prompt"]
    assert "庭院" in updated["environment_prompt"]
    assert updated["source_hash"] != source["source_hash"]
    assert updated["images"] == []
    with pytest.raises(AppError):
        dispatch(
            service,
            "select-shot-image",
            {"run_id": run_id, "shot_id": "s1", "asset_id": result["asset_ids"][0]},
        )


def test_legacy_failed_run_exposes_saved_shot_batches(service, shot_studio):
    from novel_anime.persistence.database import RunRow

    chapter, run_id = shot_studio
    with service.sessions.begin() as session:
        row = session.get(RunRow, run_id)
        result = dict(row.result)
        result["shot_batches"] = {"scene1": result.pop("shots")}
        row.result = result
        row.status = "failed"
    assert len(service.list_runs(chapter)[0]["result"]["shots"]) == 1
    assert len(service.chapter_overview(chapter)["shots"]) == 1
    assert service.shot_image_source(run_id, "s1")["character_prompt"] == "黑发少年，蓝色长袍"


def test_unchanged_appearance_is_blank_but_image_source_inherits(service, shot_studio):
    from novel_anime.persistence.database import RunRow

    _, run_id = shot_studio
    with service.sessions.begin() as session:
        row = session.get(RunRow, run_id)
        result = dict(row.result)
        first = dict(result["shots"][0])
        character_id = first["character_ids"][0]
        first["character_prompts"] = [{"character_id": character_id, "prompt": "黑发，蓝色长袍"}]
        second = {
            **first,
            "shot_id": "s2",
            "order": 2,
            "character_prompts": [],
            "character_prompt": "",
        }
        third = {
            **second,
            "shot_id": "s3",
            "order": 3,
            "character_prompts": [{"character_id": character_id, "prompt": "黑发，湿透的蓝色长袍"}],
        }
        result["shots"] = [first, second, third]
        row.result = result
        chapter_id = row.chapter_id
    assert service.shot_image_source(run_id, "s2")["character_prompt"] == "黑发，蓝色长袍"
    assert service.shot_image_source(run_id, "s3")["character_prompt"] == "黑发，湿透的蓝色长袍"
    shots = service.chapter_overview(chapter_id)["shots"]
    assert shots[1]["subjects"][0]["description"] == ""
    assert shots[2]["subjects"][0]["description"] == "黑发，湿透的蓝色长袍"


def test_image_layers_upload_adopt_and_pass_actual_references(service, shot_studio, monkeypatch):
    from novel_anime.integrations.image_generation.client import ImageGenerationClient

    _, run_id = shot_studio
    source = service.shot_image_source(run_id, "s1")
    cid = source["subjects"][0]["character_id"]
    base = {
        "run_id": run_id,
        "shot_id": "s1",
        "source_hash": source["source_hash"],
        "global_prompt": "水彩风格，用于影片分镜预览",
    }
    url = "data:image/png;base64," + base64.b64encode(_png_1x1()).decode("ascii")
    char = dispatch(
        service,
        "upload-shot-image",
        {**base, "kind": "character", "subject_id": cid, "data_url": url},
    )["asset_ids"][0]
    env = dispatch(service, "upload-shot-image", {**base, "kind": "environment", "data_url": url})[
        "asset_ids"
    ][0]
    for ident in [char, env]:
        service.select_shot_image(run_id, "s1", ident)
    captured = []

    def generate(client, prompt, count, references=None):
        captured.append((prompt, references))
        return [(_png_1x1(), "image/png")]

    monkeypatch.setattr(ImageGenerationClient, "generate", generate)
    whole = dispatch(
        service,
        "generate-shot-image",
        {**base, "reference_assets": [char, env], "extra": "保留图一的人脸，使用图二的环境"},
    )["asset_ids"][0]
    assert captured[0][1] == [(_png_1x1(), "image/png")] * 2
    assert "参考图1用于参考角色外观" in captured[0][0]
    assert "「图一」，即本次输入的第1张图片" in captured[0][0]
    assert "「图二」，即本次输入的第2张图片" in captured[0][0]
    assert "保留图一的人脸，使用图二的环境" in captured[0][0]
    with pytest.raises(AppError, match="未选中的参考图"):
        dispatch(
            service,
            "generate-shot-image",
            {**base, "reference_assets": [char], "extra": "使用图二的环境"},
        )
    assert len(captured) == 1
    service.select_shot_image(run_id, "s1", whole)
    images = service.shot_image_source(run_id, "s1")["images"]
    assert {item["asset_id"] for item in images if item["selected"]} == {char, env, whole}
    assert next(item for item in images if item["asset_id"] == whole)["reference_assets"] == [
        char,
        env,
    ]
    dispatch(service, "generate-shot-image", {**base, "kind": "character", "subject_id": cid})
    assert "只绘制此角色" in captured[-1][0]
    assert "【环境描述】" not in captured[-1][0]
    dispatch(service, "generate-shot-image", {**base, "kind": "environment"})
    assert "不出现人物" in captured[-1][0]
    assert "【人物描述】" not in captured[-1][0]
    with pytest.raises(AppError):
        dispatch(
            service, "generate-shot-image", {**base, "reference_assets": ["not-current-source"]}
        )
    with pytest.raises(AppError):
        dispatch(
            service,
            "generate-shot-image",
            {**base, "kind": "character", "subject_id": "not-present"},
        )
