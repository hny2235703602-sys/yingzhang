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
