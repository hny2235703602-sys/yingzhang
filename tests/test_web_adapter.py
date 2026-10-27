from pathlib import Path

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
