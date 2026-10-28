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


def test_persistent_chapter_and_four_pages(ui_service: StudioService) -> None:
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
    for page in ["故事脚本", "视频提示词", "角色资料", "文本任务"]:
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
    next(button for button in app.button if button.label == "开始生成故事脚本").click().run()
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
    assert ui_service.list_runs(chapter)[0]["status"] == "paused"


def test_video_page_compiles_prompt_groups(ui_service: StudioService) -> None:
    from novel_anime.domain.models import ModelProfile
    from novel_anime.persistence.database import RunRow

    work = ui_service.create_work("视频测试")
    chapter = ui_service.save_chapter(work, "第一章", "少年走进山谷。", "")
    profile = ModelProfile(
        provider="test",
        protocol="chat_completions",
        base_url="https://example.com/v1",
        model="test",
    ).model_dump()
    run_id = ui_service.create_run(chapter, profile, "二维动画", "16:9", "", "ui-video")
    shots = [
        {
            "shot_id": f"SH00{index}",
            "order": index,
            "duration_seconds": seconds,
            "shot_size": "中景",
            "camera": "固定",
            "frame": "少年走过石阶",
            "action": "少年向前走",
            "dialogue": "",
            "character_ids": ["hero"],
            "setting": "山谷石阶",
            "source_paragraph_ids": ["P0001"],
            "generation_status": "not_submitted",
        }
        for index, seconds in ((1, 13.0), (2, 6.0))
    ]
    with ui_service.sessions.begin() as session:
        row = session.get(RunRow, run_id)
        row.result = {
            "script": {
                "characters": [
                    {
                        "character_id": "hero",
                        "name": "少年",
                        "appearance": "黑发少年",
                        "costume": "青色长衫",
                    }
                ],
                "shots": shots,
            },
            "shots": shots,
            "paragraphs": [{"paragraph_id": "P0001", "text": "少年走进山谷。"}],
        }
        row.status = "succeeded"
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    app.selectbox(key=f"chapter_choice_{work}").set_value(chapter).run()
    app.radio[0].set_value("视频提示词").run()
    assert_clean(app)
    headings = [item.value for item in app.subheader]
    assert any("G001 · 19 秒" in value for value in headings), headings
    assert any("Wan2.2 TI2V-5B" in value for value in headings), headings
    prompts = [field.value for field in app.text_area]
    assert any(
        "山谷石阶" in value and "0-13秒：" in value and "13-19秒：" in value for value in prompts
    ), prompts
    assert any("接上一段最后一帧继续" in item.value for item in app.code), [
        item.value for item in app.code
    ]


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
