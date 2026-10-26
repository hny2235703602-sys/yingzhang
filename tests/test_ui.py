"""UI regressions use a migrated temporary database and never call external models."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from streamlit.testing.v1 import AppTest

from novel_anime.application.service import StudioService
from novel_anime.core.settings import ROOT


@pytest.fixture
def ui_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> StudioService:
    monkeypatch.setenv("YINGZHANG_DATA_DIR", str(tmp_path))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")
    return StudioService()


def assert_clean(app: AppTest) -> None:
    assert not app.exception, [error.message for error in app.exception]
    assert not app.error, [error.value for error in app.error]


def test_persistent_chapter_and_six_pages(ui_service: StudioService) -> None:
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    assert_clean(app)
    app.text_input[0].set_value("UI验证作品")
    next(button for button in app.button if button.label == "创建作品").click().run()
    assert_clean(app)
    next(field for field in app.text_input if field.label == "章节标题").set_value("第一章")
    next(field for field in app.text_area if field.label == "章节正文").set_value("少年走进庭院。")
    next(button for button in app.button if button.label == "保存持久草稿").click().run()
    assert_clean(app)
    work = ui_service.list_works()[0]
    chapter = ui_service.list_chapters(work["id"])[0]
    assert ui_service.get_chapter(chapter["id"])["text"] == "少年走进庭院。"
    for page in ["改编大纲", "分镜工作台", "本章分析与角色资料", "文本任务"]:
        app.radio[0].set_value(page).run()
        assert_clean(app)
    assert "模型与工作流" not in app.radio[0].options
    next(button for button in app.button if button.label == "模型与工作流").click().run()
    assert_clean(app)
    assert app.title[0].value == "设置"
    next(button for button in app.button if button.label == "返回创作空间").click().run()
    assert_clean(app)
    assert app.title[0].value == "文本任务"
    app.radio[0].set_value("章节原文").run()
    body = next(field for field in app.text_area if field.label == "章节正文")
    assert body.value == "少年走进庭院。"
    body.set_value("少年走进庭院，停在廊下。")
    next(button for button in app.button if button.label == "保存持久草稿").click().run()
    assert_clean(app)
    assert ui_service.get_chapter(chapter["id"])["revision"] == 2


def test_submit_and_pause_use_real_service_without_worker(ui_service: StudioService) -> None:
    work = ui_service.create_work("文本测试")
    chapter = ui_service.save_chapter(work, "第一章", "少年走进庭院。", "")
    ui_service.save_profile(
        {
            "profile_id": "local",
            "provider": "LM Studio",
            "protocol": "chat_completions",
            "base_url": "http://127.0.0.1:1234/v1",
            "model": "test",
            "api_key_env": "",
        }
    )
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    app.selectbox(key=f"chapter_choice_{work}").set_value(chapter).run()
    next(button for button in app.button if button.label == "开始文本改编").click().run()
    assert_clean(app)
    runs = ui_service.list_runs(chapter)
    assert len(runs) == 1
    assert runs[0]["status"] == "pending"
    assert runs[0]["document"]["text"] == "少年走进庭院。"
    app.run()
    assert len(ui_service.list_runs(chapter)) == 1
    app.radio[0].set_value("文本任务").run()
    next(button for button in app.button if button.label == "暂停").click().run()
    assert_clean(app)
    assert ui_service.list_runs(chapter)[0]["status"] == "pause_requested"


def test_fetch_and_select_model(ui_service: StudioService, monkeypatch: pytest.MonkeyPatch) -> None:
    from novel_anime.integrations.language_models.client import LLMClient

    requested = []

    def catalog(client: LLMClient) -> list[str]:
        requested.append(client.profile.base_url)
        return ["model-a", "model-b"]

    monkeypatch.setattr(LLMClient, "list_models", catalog)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    next(button for button in app.button if button.label == "模型与工作流").click().run()
    next(field for field in app.text_input if field.label == "服务地址").set_value(
        "https://models.test/v1"
    )
    next(field for field in app.text_input if field.label == "提供商").set_value("test")
    next(button for button in app.button if button.label == "获取可用模型").click().run()
    assert_clean(app)
    assert requested == ["https://models.test/v1"]
    next(field for field in app.selectbox if field.label == "可用模型").set_value("model-b")
    next(button for button in app.button if button.label == "保存模型配置").click().run()
    assert_clean(app)
    assert ui_service.load_profiles()["online"]["model"] == "model-b"
    assert len(requested) == 1
