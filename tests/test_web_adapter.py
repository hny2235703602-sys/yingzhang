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
