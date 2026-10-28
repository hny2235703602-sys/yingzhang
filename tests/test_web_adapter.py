import base64
import os
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
    text = {"script": "按「时长、分镜、动作、台词」写故事脚本。"}
    dispatch(service, "prompts", {"category": "text", **text})
    state = snapshot(StudioService())
    assert state["templates"] == text
    assert state["media_templates"] == {
        "image": "角色：{name}，外貌：{appearance}",
        "video": "保持角色连续性",
    }
    with pytest.raises(AppError):
        dispatch(service, "prompts", {"category": "image", "template": " "})
    with pytest.raises(AppError):
        dispatch(service, "prompts", {"category": "text", "script": " "})
    with pytest.raises(AppError):
        dispatch(service, "prompts", {"category": "text", "analysis": "旧提示词"})
    assert snapshot(service)["media_templates"] == state["media_templates"]
    assert snapshot(service)["templates"] == text


def test_missing_shipped_prompt_never_breaks_the_workspace(service, monkeypatch, tmp_path):
    from novel_anime.core import settings
    from novel_anime.ui import web_server

    monkeypatch.setattr(settings, "ROOT", tmp_path)
    state = web_server.snapshot(service)
    assert state["templates"] == {"script": ""}
    assert state["media_templates"] == {"image": "", "video": ""}
    assert web_server.snapshot_image_prompt(service) == ""


def test_http_layer_answers_state_and_reports_unknown_chapter(service):
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import urlopen

    from novel_anime.ui.web_server import Handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(f"{base}/api/state", timeout=10) as response:
            assert response.status == 200
            assert json.loads(response.read())["novels"] == []
        with pytest.raises(HTTPError) as caught:
            urlopen(f"{base}/api/overview/missing", timeout=10)
        assert caught.value.code == 404
        assert json.loads(caught.value.read()) == {
            "error": "未找到章节。",
            "code": "not_found",
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


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
    shots = [
        {
            "shot_id": "s2",
            "order": 2,
            "duration_seconds": 4.0,
            "shot_size": "远景",
            "camera": "固定",
            "frame": "庭院空镜",
            "action": "无人",
            "character_ids": [],
            "setting": "庭院",
            "source_paragraph_ids": ["P0001"],
        },
        {
            "shot_id": "s1",
            "order": 1,
            "duration_seconds": 5.0,
            "shot_size": "近景",
            "camera": "缓慢推进",
            "frame": "少年推门",
            "action": "少年推门",
            "character_ids": ["hero"],
            "setting": "门边",
            "source_paragraph_ids": ["P0001"],
        },
    ]
    with service.sessions.begin() as session:
        row = session.get(RunRow, run)
        row.result = {
            "script": {
                "characters": [
                    {
                        "character_id": "hero",
                        "name": "少年",
                        "appearance": "黑色长发",
                        "costume": "蓝色长袍",
                    }
                ],
                "shots": shots,
            },
            "shots": shots,
            "paragraphs": [{"paragraph_id": "P0001", "text": "少年推门。"}],
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
    assert result["shots"][0]["subjects"][0]["description"] == "黑色长发；蓝色长袍"
    assert result["shots"][0]["image_source"]["character_prompt"] == "少年：黑色长发；蓝色长袍"
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
    if os.name == "posix":  # Windows has no POSIX mode bits; production runs Linux.
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
        "order": 1,
        "duration_seconds": 5.0,
        "shot_size": "中景",
        "camera": "缓慢推进",
        "frame": "人物在画面左侧，少年抬头",
        "action": "少年抬头",
        "dialogue": "少年：“有人吗？”",
        "character_ids": ["hero"],
        "setting": "清晨庭院，石板路，竹影",
        "source_paragraph_ids": ["P0001"],
        "generation_status": "not_submitted",
    }
    with service.sessions.begin() as session:
        row = session.get(RunRow, run_id)
        row.result = {
            "script": {
                "characters": [
                    {
                        "character_id": "hero",
                        "name": "少年",
                        "appearance": "黑发少年",
                        "costume": "蓝色长袍",
                    }
                ],
                "shots": [shot],
            },
            "shots": [shot],
            "paragraphs": [{"paragraph_id": "P0001", "text": "少年站在庭院。"}],
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
        script = dict(result_data["script"])
        script["characters"] = []
        shot = {**script["shots"][0], "setting": ""}
        script["shots"] = [shot]
        result_data["script"] = script
        result_data["shots"] = [shot]
        row.result = result_data
    updated = service.shot_image_source(run_id, "s1")
    assert "请重新生成脚本" in updated["character_prompt"]
    assert "请先补充场景描述" in updated["environment_prompt"]
    assert updated["source_hash"] != source["source_hash"]
    assert updated["images"] == []
    with pytest.raises(AppError):
        dispatch(
            service,
            "select-shot-image",
            {"run_id": run_id, "shot_id": "s1", "asset_id": result["asset_ids"][0]},
        )


def test_old_four_stage_results_are_not_offered_as_script(service, shot_studio):
    from novel_anime.persistence.database import RunRow

    chapter, run_id = shot_studio
    with service.sessions.begin() as session:
        row = session.get(RunRow, run_id)
        row.result = {
            "analysis": {"characters": []},
            "outline": {"scenes": []},
            "shots": [
                {
                    "shot_id": "s1",
                    "order": 1,
                    "character_ids": ["hero"],
                    "character_prompt": "黑发少年，蓝色长袍",
                }
            ],
        }
        row.status = "succeeded"
    listed = service.list_runs(chapter)[0]
    assert listed["completed_stages"] == []
    assert service.chapter_overview(chapter)["shots"] == []


def test_overview_prefers_script_appearance_and_handles_shots_without_people(service, shot_studio):
    from novel_anime.persistence.database import RunRow

    chapter, run_id = shot_studio
    with service.sessions.begin() as session:
        row = session.get(RunRow, run_id)
        result = dict(row.result)
        script = dict(result["script"])
        first = dict(script["shots"][0])
        second = {**first, "shot_id": "s2", "order": 2, "character_ids": []}
        third = {**first, "shot_id": "s3", "order": 3, "character_ids": ["stranger"]}
        script["shots"] = [first, second, third]
        result["script"] = script
        result["shots"] = script["shots"]
        row.result = result
    overview = service.chapter_overview(chapter)
    assert [shot["shot_id"] for shot in overview["shots"]] == ["s1", "s2", "s3"]
    assert overview["shots"][1]["subjects"] == []
    assert overview["shots"][1]["image_source"]["character_prompt"] == "无人物"
    assert overview["shots"][0]["subjects"][0]["description"] == "黑发少年；蓝色长袍"
    assert overview["shots"][2]["subjects"][0]["name"] == "stranger"
    assert overview["shots"][2]["subjects"][0]["description"] == ""


def test_script_rows_are_exposed_as_shots_even_without_a_stored_copy(service, shot_studio):
    from novel_anime.persistence.database import RunRow

    _, run_id = shot_studio
    with service.sessions.begin() as session:
        row = session.get(RunRow, run_id)
        result = dict(row.result)
        result.pop("shots")
        row.result = result
    run = service.get_run(run_id)
    assert run["result"]["shots"] == run["result"]["script"]["shots"]
    assert service.shot_image_source(run_id, "s1")["character_prompt"] == "少年：黑发少年；蓝色长袍"


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


def test_manual_image_prompts_replace_automatic_content_and_persist(
    service, shot_studio, monkeypatch
):
    from novel_anime.integrations.image_generation.client import ImageGenerationClient

    _, run_id = shot_studio
    source = service.shot_image_source(run_id, "s1")
    captured = []

    def generate(client, prompt, count, references=None):
        captured.append((prompt, references))
        return [(_png_1x1(), "image/png")]

    monkeypatch.setattr(ImageGenerationClient, "generate", generate)
    dispatch(
        service,
        "generate-shot-image",
        {
            "run_id": run_id,
            "shot_id": "s1",
            "source_hash": source["source_hash"],
            "global_prompt": "水彩风格，用于影片分镜预览",
            "positive_prompt": "  月光下的雪山，写实摄影  ",
            "negative_prompt": "  水印、多余人物  ",
            "reference_assets": [],
        },
    )
    prompt, references = captured[0]
    assert prompt == "月光下的雪山，写实摄影\n\n【反向提示词 · 请避免以下内容】\n水印、多余人物"
    assert references is None
    saved = service.shot_image_source(run_id, "s1")["images"]
    assert saved[0]["prompt"] == prompt
    assert saved[0]["reference_assets"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"positive_prompt": " "},
        {"positive_prompt": 42},
        {"positive_prompt": "x" * 20001},
        {"negative_prompt": []},
        {"negative_prompt": "x" * 10001},
        {"positive_prompt": "使用图一的构图"},
    ],
)
def test_manual_image_prompts_validate_before_calling_model(
    service, shot_studio, monkeypatch, changes
):
    from novel_anime.integrations.image_generation.client import ImageGenerationClient

    _, run_id = shot_studio
    source = service.shot_image_source(run_id, "s1")
    monkeypatch.setattr(
        ImageGenerationClient, "generate", lambda *args, **kwargs: pytest.fail("invalid request")
    )
    with pytest.raises(AppError):
        dispatch(
            service,
            "generate-shot-image",
            {
                "run_id": run_id,
                "shot_id": "s1",
                "source_hash": source["source_hash"],
                "global_prompt": "水彩风格，用于影片分镜预览",
                "positive_prompt": "雪山",
                **changes,
            },
        )


def test_manual_image_prompts_forward_optional_uploaded_reference(
    service, shot_studio, monkeypatch
):
    from novel_anime.integrations.image_generation.client import ImageGenerationClient

    _, run_id = shot_studio
    source = service.shot_image_source(run_id, "s1")
    body = {
        "run_id": run_id,
        "shot_id": "s1",
        "source_hash": source["source_hash"],
        "global_prompt": "水彩风格，用于影片分镜预览",
    }
    uploaded = dispatch(
        service,
        "upload-shot-image",
        {
            **body,
            "positive_prompt": "",
            "data_url": "data:image/png;base64," + base64.b64encode(_png_1x1()).decode(),
        },
    )["asset_ids"][0]
    captured = []

    def generate(client, prompt, count, references=None):
        captured.append((prompt, references))
        return [(_png_1x1(), "image/png")]

    monkeypatch.setattr(ImageGenerationClient, "generate", generate)
    dispatch(
        service,
        "generate-shot-image",
        {
            **body,
            "positive_prompt": "参考图一的构图，月光下的雪山",
            "negative_prompt": "水印",
            "reference_assets": [uploaded],
        },
    )
    assert captured[0][1] == [(_png_1x1(), "image/png")]
    assert "参考图一的构图" in captured[0][0]
    assert "第1张图片" in captured[0][0]
    assert "水印" in captured[0][0]


def test_library_prompt_snapshot_is_durable_and_idempotent(service, shot_studio):
    from novel_anime.application.library import list_entries
    from novel_anime.persistence.database import RunRow

    chapter_id, run_id = shot_studio
    work_id = service.get_chapter(chapter_id)["work_id"]
    body = {"kind": "prompts", "work_id": work_id, "run_id": run_id, "revision": 1}
    first = dispatch(service, "library-save", body)
    assert first["existing"] is False
    assert dispatch(service, "library-save", body) == {"id": first["id"], "existing": True}
    with service.sessions.begin() as session:
        run = session.get(RunRow, run_id)
        result = dict(run.result)
        script = dict(result["script"])
        script["shots"] = [{**script["shots"][0], "action": "少年转身"}]
        result["script"] = script
        run.result = result
        run.revision += 1
    with pytest.raises(AppError, match="脚本版本已更新"):
        dispatch(service, "library-save", body)
    second = dispatch(service, "library-save", {**body, "revision": 2})
    assert second["id"] != first["id"]
    entries = list_entries(StudioService(), work_id)
    assert len(entries) == 2
    assert (
        next(item for item in entries if item["id"] == first["id"])["content"]["script"]["shots"][
            0
        ]["action"]
        == "少年抬头"
    )
    wrong_work = service.create_work("其他作品")
    with pytest.raises(AppError):
        dispatch(service, "library-save", {**body, "work_id": wrong_work, "revision": 2})
    with pytest.raises(AppError):
        dispatch(service, "library-remove", {"work_id": wrong_work, "id": first["id"]})
    dispatch(service, "library-remove", {"work_id": work_id, "id": first["id"]})
    assert len(list_entries(service, work_id)) == 1
    assert service.get_run(run_id)["result"]["script"]["shots"][0]["action"] == "少年转身"


def test_library_images_archive_files_and_separate_subjects(service, shot_studio, tmp_path):
    from novel_anime.application.library import list_entries
    from novel_anime.persistence.database import ShotImageRow

    chapter_id, run_id = shot_studio
    work_id = service.get_chapter(chapter_id)["work_id"]
    asset_ids = []
    for kind, subject in [("character", "hero"), ("character", "hero"), ("environment", "")]:
        asset_id = dispatch(
            service,
            "upload-shot-image",
            {
                "run_id": run_id,
                "shot_id": "s1",
                "kind": kind,
                "subject_id": subject,
                "source_hash": service.shot_image_source(run_id, "s1")["source_hash"],
                "global_prompt": "水彩风格，用于影片分镜预览",
                "data_url": "data:image/png;base64," + base64.b64encode(_png_1x1()).decode(),
            },
        )["asset_ids"][0]
        asset_ids.append(asset_id)
        dispatch(
            service,
            "library-save",
            {
                "work_id": work_id,
                "kind": "images",
                "asset_id": asset_id,
            },
        )
    entries = list_entries(service, work_id)
    assert len(entries) == 3
    assert len({item["content"]["group_key"] for item in entries}) == 2
    assert {item["content"]["name"] for item in entries} == {"少年", "环境与场景"}
    with service.sessions() as session:
        original = session.get(ShotImageRow, asset_ids[0])
        (tmp_path / "assets" / "shots" / original.filename).unlink()
    for entry in entries:
        archived = tmp_path / "assets" / "library_images" / Path(entry["content"]["url"]).name
        assert archived.read_bytes() == _png_1x1()


def test_library_video_validates_chapter_url_and_file(service, tmp_path):
    from novel_anime.application.library import list_entries

    work = service.create_work("视频归档")
    chapter = service.save_chapter(work, "第一章", "正文", "")
    other = service.create_work("别的作品")
    body = {"kind": "videos", "work_id": work, "chapter_id": chapter, "title": "第一版"}
    for url in ["javascript:alert(1)", "file:///tmp/a.mp4", "https://user:pass@host/a.mp4", ""]:
        with pytest.raises(AppError):
            dispatch(service, "library-save", {**body, "url": url})
    with pytest.raises(AppError):
        dispatch(service, "library-save", {**body, "work_id": other, "url": "https://host/a.mp4"})
    raw = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 12
    uploaded = dispatch(
        service,
        "library-save",
        {
            **body,
            "data_url": "data:video/mp4;base64," + base64.b64encode(raw).decode(),
        },
    )
    assert not uploaded["existing"]
    archived = list_entries(service, work)[0]["content"]["url"]
    assert (tmp_path / "assets" / "library_videos" / Path(archived).name).read_bytes() == raw
    with pytest.raises(AppError):
        dispatch(
            service,
            "library-save",
            {
                **body,
                "data_url": "data:video/mp4;base64," + base64.b64encode(b"invalid file").decode(),
            },
        )
    direct = dispatch(service, "library-save", {**body, "url": "https://host/a.mp4"})
    assert not direct["existing"]
    assert len(list_entries(StudioService(), work)) == 2


def test_library_video_http_ranges_and_static_assets(service, tmp_path):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from novel_anime.ui.web_server import Handler

    raw = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 12
    filename = "a" * 64 + ".mp4"
    root = tmp_path / "assets" / "library_videos"
    root.mkdir(parents=True)
    (root / filename).write_bytes(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_port}"
    try:
        for asset in ["library.js", "library.css", "tables.js", "tables.css"]:
            with urlopen(address + "/" + asset) as response:
                assert response.status == 200
                assert len(response.read()) > 100
        url = address + "/assets/library-videos/" + filename
        for header, expected in [("bytes=4-7", raw[4:8]), ("bytes=-4", raw[-4:])]:
            with urlopen(Request(url, headers={"Range": header})) as response:
                assert response.status == 206
                assert response.read() == expected
                assert response.headers["Accept-Ranges"] == "bytes"
        with pytest.raises(HTTPError) as caught:
            urlopen(Request(url, headers={"Range": "bytes=999-"}))
        assert caught.value.code == 416
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_library_collects_independent_studio_images_into_named_work_rows(service, tmp_path):
    from novel_anime.application.library import list_entries

    work = service.create_work("独立图像收藏")
    asset_id = dispatch(
        service,
        "studio-upload",
        {
            "data_url": "data:image/png;base64," + base64.b64encode(_png_1x1()).decode(),
        },
    )["asset_ids"][0]
    body = {
        "kind": "images",
        "work_id": work,
        "asset_id": asset_id,
        "image_kind": "character",
        "name": "宋游",
    }
    for name in ["", " ", "a" * 121]:
        with pytest.raises(AppError):
            dispatch(service, "library-save", {**body, "name": name})
    collected = dispatch(service, "library-save", body)
    assert not collected["existing"]
    entry = list_entries(StudioService(), work)[0]
    assert entry["chapter_id"] == ""
    assert entry["content"]["name"] == "宋游"
    assert entry["content"]["group_key"] == "character:宋游"
    original = next((tmp_path / "assets" / "studio").iterdir())
    original.unlink()
    archived = tmp_path / "assets" / "library_images" / Path(entry["content"]["url"]).name
    assert archived.read_bytes() == _png_1x1()


def test_independent_image_studio_does_not_require_any_work_or_chapter(
    service, monkeypatch, tmp_path
):
    from novel_anime.application.image_studio import list_images
    from novel_anime.integrations.image_generation.client import ImageGenerationClient

    dispatch(
        service,
        "image-profile",
        {
            "name": "测试生图",
            "model": "illustrator",
            "base_url": "https://images.example/v1",
            "api_key": "test-image-key",
            "timeout": 90,
        },
    )
    captured = []

    def generate(client, prompt, count, references=None):
        captured.append((prompt, references))
        return [(_png_1x1(), "image/png") for _ in range(count)]

    monkeypatch.setattr(ImageGenerationClient, "generate", generate)
    dispatch(
        service,
        "studio-generate",
        {
            "positive_prompt": "手写雪山月夜",
            "negative_prompt": "水印",
            "count": 2,
            "reference_assets": [],
        },
    )
    assert captured[0] == (
        "手写雪山月夜\n\n【反向提示词 · 请避免以下内容】\n水印",
        None,
    )
    rows = list_images(service)
    assert len(rows) == 2
    assert all(row["positive_prompt"] == "手写雪山月夜" for row in rows)
    assert all(row["negative_prompt"] == "水印" for row in rows)
    assert all(row["reference_assets"] == [] for row in rows)

    reference_id = dispatch(
        service,
        "studio-upload",
        {"data_url": "data:image/png;base64," + base64.b64encode(_png_1x1()).decode()},
    )["asset_ids"][0]
    remix = dispatch(
        service,
        "studio-generate",
        {
            "positive_prompt": "根据图一的颜色，绘制一轮明月",
            "negative_prompt": "文字",
            "reference_assets": [reference_id],
        },
    )
    assert remix["asset_ids"]
    assert "本次输入的第1张图片" in captured[-1][0]
    assert captured[-1][1] == [(_png_1x1(), "image/png")]
    assert len(list_images(service)) == 4
    assert len(service.list_works()) == 0
    assert list((tmp_path / "assets" / "studio").glob("*.png"))


def test_independent_image_studio_rejects_missing_reference_or_prompt(service):
    from novel_anime.application.image_studio import create_images

    with pytest.raises(AppError):
        create_images(service, {"positive_prompt": "图一的构图", "reference_assets": ["a" * 32]})
    with pytest.raises(AppError):
        create_images(service, {"positive_prompt": "  "})


def test_library_prompt_groups_match_saved_shots_and_survive_config_changes(service, shot_studio):
    from novel_anime.application.library import list_entries
    from novel_anime.persistence.database import LibraryEntryRow, RunRow

    chapter, run_id = shot_studio
    work = service.get_chapter(chapter)["work_id"]
    with service.sessions.begin() as session:
        run = session.get(RunRow, run_id)
        result = dict(run.result)
        script = dict(result["script"])
        base = script["shots"][0]
        script["shots"] = [
            {**base, "shot_id": f"s{index}", "order": index, "duration_seconds": seconds}
            for index, seconds in [(1, 13), (2, 6), (3, 8)]
        ]
        result["script"] = script
        result["shots"] = script["shots"]
        run.result = result
    service.save_video_config({"target_seconds": 15, "max_seconds": 20})
    saved = dispatch(
        service, "library-save", {"kind": "prompts", "work_id": work, "run_id": run_id}
    )
    groups = list_entries(service, work)[0]["content"]["groups"]
    assert [(group["group_id"], group["shot_ids"]) for group in groups] == [
        ("G001", ["s1", "s2"]),
        ("G002", ["s3"]),
    ]
    assert "0-13秒" in groups[0]["prompt"]
    assert "13-19秒" in groups[0]["prompt"]
    assert [shot["shot_id"] for shot in groups[0]["shot_prompts"]] == ["s1", "s2"]
    assert "少年抬头" in groups[0]["shot_prompts"][0]["prompt"]
    service.save_video_config({"target_seconds": 5, "max_seconds": 5})
    assert list_entries(StudioService(), work)[0]["content"]["groups"] == groups
    # Collections saved before grouping was introduced still render from their own snapshot.
    with service.sessions.begin() as session:
        row = session.get(LibraryEntryRow, saved["id"])
        content = dict(row.content)
        content.pop("groups")
        row.content = content
    legacy_groups = list_entries(service, work)[0]["content"]["groups"]
    assert [group["shot_ids"] for group in legacy_groups] == [["s1", "s2"], ["s3"]]


def test_video_outputs_enter_archive_only_after_manual_chapter_selection(service):
    from novel_anime.application.library import list_entries

    work = service.create_work("手动视频归档")
    chapter_one = service.save_chapter(work, "第一章", "雨落下。", "")
    chapter_two = service.save_chapter(work, "第二章", "天色放晴。", "")

    assert service.video_plan(chapter_one)["groups"] == []
    assert list_entries(service, work) == []

    dispatch(
        service,
        "library-save",
        {
            "work_id": work,
            "kind": "videos",
            "chapter_id": chapter_two,
            "title": "第二章成片",
            "url": "https://videos.example/second-chapter.mp4",
        },
    )
    archived = list_entries(service, work)
    assert len(archived) == 1
    assert archived[0]["chapter_id"] == chapter_two
    assert archived[0]["content"]["title"] == "第二章成片"


def test_keyframe_prompt_describes_still_image_without_video_timing_or_dialogue():
    from novel_anime.application.library import keyframe_prompt

    prompt = keyframe_prompt(
        {
            "shot_id": "s1",
            "shot_size": "近景",
            "camera": "缓慢推进",
            "duration_seconds": 9,
            "setting": "月光下的竹林",
            "frame": "人物在画面左侧，右侧留白",
            "action": "少年抬手，竹林传来蝉鸣。",
            "dialogue": "少年：别动！",
            "character_ids": ["hero"],
        },
        [
            {
                "character_id": "hero",
                "name": "宋游",
                "appearance": "黑发束起",
                "costume": "蓝色长袍",
            },
            {"character_id": "other", "name": "未出场人物"},
        ],
        "国风水彩",
        "9:16",
    )
    for value in [
        "起始关键帧",
        "国风水彩",
        "9:16",
        "近景",
        "月光下的竹林",
        "画面左侧",
        "宋游",
        "黑发束起",
        "蓝色长袍",
        "定格瞬间",
    ]:
        assert value in prompt
    for value in ["缓慢推进", "9 秒", "别动", "未出场人物", "蝉鸣", "。。"]:
        assert value not in prompt


def test_library_keyframe_prompts_are_saved_and_added_to_older_collections(service, shot_studio):
    import copy

    from novel_anime.application.library import list_entries
    from novel_anime.persistence.database import LibraryEntryRow

    chapter, run_id = shot_studio
    work = service.get_chapter(chapter)["work_id"]
    saved = dispatch(
        service, "library-save", {"work_id": work, "kind": "prompts", "run_id": run_id}
    )
    with service.sessions() as session:
        row = session.get(LibraryEntryRow, saved["id"])
        stored = row.content["groups"][0]["shot_prompts"][0]["keyframe_prompt"]
        assert "少年" in stored
        assert "蓝色长袍" in stored
    with service.sessions.begin() as session:
        row = session.get(LibraryEntryRow, saved["id"])
        content = copy.deepcopy(row.content)
        del content["groups"][0]["shot_prompts"][0]["keyframe_prompt"]
        row.content = content
    old = list_entries(StudioService(), work)[0]["content"]["groups"][0]["shot_prompts"][0]
    assert old["keyframe_prompt"] == stored
    assert old["prompt"]


def test_gallery_people_can_have_empty_rows_and_survive_removing_last_image(service):
    from novel_anime.application.library import list_entries, list_people

    work = service.create_work("人物空行")
    other = service.create_work("其他作品")
    person = dispatch(service, "gallery-person-create", {"work_id": work, "name": " 宋游 "})
    second = dispatch(service, "gallery-person-create", {"work_id": work, "name": "阿梨"})
    assert person["name"] == "宋游"
    assert list_entries(service, work) == []
    assert [row["id"] for row in list_people(StudioService(), work)] == [person["id"], second["id"]]
    duplicate = dispatch(service, "gallery-person-create", {"work_id": work, "name": "宋游"})
    assert duplicate["existing"] and duplicate["id"] == person["id"]
    assert list_people(service, other) == []
    for name in ["", " ", "a" * 121, None, 42]:
        with pytest.raises(AppError):
            dispatch(service, "gallery-person-create", {"work_id": work, "name": name})
    with pytest.raises(AppError):
        dispatch(service, "gallery-person-create", {"work_id": "missing", "name": "人物"})

    asset = dispatch(service, "studio-upload", {
        "data_url": "data:image/png;base64," + base64.b64encode(_png_1x1()).decode()
    })["asset_ids"][0]
    body = {"kind": "images", "work_id": work, "asset_id": asset,
            "name": "宋游", "person_id": person["id"]}
    with pytest.raises(AppError):
        dispatch(service, "library-save", {**body, "work_id": other})
    entry = dispatch(service, "library-save", body)
    content = list_entries(service, work)[0]["content"]
    assert content["person_id"] == person["id"]
    assert content["group_key"] == "character:宋游"
    dispatch(service, "library-remove", {"work_id": work, "id": entry["id"]})
    assert len(list_people(StudioService(), work)) == 2
    assert list_entries(service, work) == []


def test_generation_history_keeps_eight_even_when_collected(service, monkeypatch, tmp_path):
    from novel_anime.application.image_studio import image_asset, list_images
    from novel_anime.application.library import list_entries
    from novel_anime.integrations.image_generation.client import ImageGenerationClient
    from novel_anime.persistence.database import StudioImageRow

    service._save_config("image_profile", {"backend": "mflux", "model": "test"})
    monkeypatch.setattr(ImageGenerationClient, "generate", lambda *args, **kwargs: [
        (_png_1x1(), "image/png") for _ in range(args[2])
    ])
    work = service.create_work("图库副本")
    reference = dispatch(service, "studio-upload", {
        "data_url": "data:image/png;base64," + base64.b64encode(_png_1x1()).decode()
    })["asset_ids"][0]
    first = dispatch(service, "studio-generate", {"positive_prompt": "第一张"})["asset_ids"][0]
    original, _ = image_asset(service, first)
    dispatch(service, "library-save", {
        "work_id": work, "kind": "images", "asset_id": first, "name": "宋游"
    })
    copied_reference = dispatch(service, "studio-reference", {"asset_id": first})["asset_ids"][0]
    for index in range(1, 5):
        dispatch(service, "studio-generate", {
            "positive_prompt": f"第 {index} 批", "count": 3,
            "reference_assets": [copied_reference]
        })
    all_rows = list_images(StudioService())
    generated = [row for row in all_rows if row["purpose"] == "generated"]
    assert len(generated) == 8
    assert generated[0]["positive_prompt"] == "第 4 批"
    assert [row["created_at"] for row in generated] == sorted(
        [row["created_at"] for row in generated], reverse=True
    )
    assert first not in [row["asset_id"] for row in all_rows]
    assert not original.exists()
    with service.sessions() as session:
        assert session.get(StudioImageRow, first) is None
    with pytest.raises(AppError):
        image_asset(service, first)
    for ident in [reference, copied_reference]:
        assert image_asset(service, ident)[0].read_bytes() == _png_1x1()
    archived = tmp_path / "assets" / "library_images" / Path(
        list_entries(StudioService(), work)[0]["content"]["url"]
    ).name
    assert archived.read_bytes() == _png_1x1()
    assert len(list((tmp_path / "assets" / "studio").glob("*.png"))) == 10


def test_gallery_migration_preserves_existing_person_groups(service):
    from novel_anime.application.library import list_people
    from novel_anime.persistence.database import LibraryEntryRow

    work = service.create_work("旧图库")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.downgrade(config, "0006")
    with service.sessions.begin() as session:
        for index, (name, kind) in enumerate([
            ("宋游", "character"), ("宋游", "character"), ("阿梨", "character"),
            ("竹林", "environment"),
        ]):
            session.add(LibraryEntryRow(
                id=str(index), work_id=work, kind="images", source_key=str(index), chapter_id="",
                created_at=f"2026-10-28T00:00:0{index}+00:00",
                content={"name": name, "image_kind": kind},
            ))
    command.upgrade(config, "head")
    assert [person["name"] for person in list_people(service, work)] == ["宋游", "阿梨"]


def test_gallery_person_http_endpoint(service):
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen

    from novel_anime.ui.web_server import Handler

    work = service.create_work("图库接口")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(Request(
            base + "/api/gallery-person-create",
            data=json.dumps({"work_id": work, "name": "宋游"}).encode(),
            headers={"Content-Type": "application/json"},
        )) as response:
            person = json.loads(response.read())
        with urlopen(base + "/api/gallery-people/" + work) as response:
            assert json.loads(response.read())[0]["id"] == person["id"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_reference_jpeg_allows_trailing_metadata(service):
    from novel_anime.application.image_studio import image_asset
    from novel_anime.ui.web_server import image_dimensions

    # JFIF/SOF dimensions followed by an end marker and exporter metadata.
    jpeg = (b'\xff\xd8\xff\xe0\x00\x04JF'
            b'\xff\xc0\x00\x0b\x08\x00\x02\x00\x03\x01\x01\x11\x00'
            b'\xff\xd9' + b'exporter metadata')
    assert image_dimensions(jpeg, 'image/jpeg') == (3, 2)
    uploaded = dispatch(service, 'studio-upload', {
        'data_url': 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode()
    })
    assert image_asset(service, uploaded['asset_ids'][0])[0].read_bytes() == jpeg
    with pytest.raises(ValueError):
        image_dimensions(jpeg.replace(b'\xff\xd9', b''), 'image/jpeg')
