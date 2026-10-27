"""Story-script stage guards: one stage only, version lineage and manual edits."""

from copy import deepcopy

import pytest
import test_service
from test_models import script

from novel_anime.core.errors import AppError
from novel_anime.domain.models import ModelProfile
from novel_anime.integrations.language_models.client import LLMClient
from novel_anime.persistence.database import RunRow
from novel_anime.workers.main import Worker


@pytest.fixture
def service(monkeypatch, tmp_path):
    return test_service.service.__wrapped__(monkeypatch, tmp_path)


@pytest.fixture
def manual(service, monkeypatch):
    story, _ = script()
    rows = [
        shot.model_dump(exclude={"shot_id", "order", "generation_status"}) for shot in story.shots
    ]
    characters = [person.model_dump() for person in story.characters]
    calls = []

    def generate(client, schema, system, payload, stage):
        calls.append(stage)
        from novel_anime.domain.models import ScriptBatch, ScriptRow

        first = payload["paragraphs"][0]["paragraph_id"]
        return (
            ScriptBatch(
                characters=story.characters,
                shots=[ScriptRow(**{**rows[0], "source_paragraph_ids": [first]})],
            ),
            {"stage": stage},
        )

    monkeypatch.setattr(LLMClient, "generate", generate)
    work = service.create_work("单步作品")
    chapter = service.save_chapter(work, "第一章", "少年推开殿门。", "")
    profile = ModelProfile(
        provider="test",
        protocol="chat_completions",
        base_url="https://example.com/v1",
        model="fake",
        api_key_env="",
    ).model_dump()

    def submit(stage=None, source=None, key=None, selected=None):
        return service.create_run(
            chapter,
            profile,
            "二维动画",
            "16:9",
            "",
            key or "script-" + str(len(calls)),
            target_stage=stage,
            source_run_id=source and source["id"],
            source_revision=source and source["revision"],
            selected_stages=selected,
        )

    return chapter, calls, {"rows": rows, "characters": characters}, submit


def process(service, run):
    worker = Worker()
    assert worker.claim() == run
    worker.process(run)
    assert service.get_run(run)["owner"] is None
    return service.get_run(run)


def test_script_is_the_only_stage(service, manual):
    chapter, calls, models, submit = manual
    for stage in ("analysis", "outline", "shots", "review"):
        with pytest.raises(AppError, match="故事脚本只有一步"):
            submit(stage)
    with pytest.raises(AppError, match="故事脚本只有一步"):
        submit("script", selected=["script", "review"])
    assert calls == []

    run = process(service, submit("script"))
    assert calls == ["script"]
    assert run["status"] == "succeeded"
    listed = service.list_runs(chapter)[0]
    assert listed["completed_stages"] == ["script"]
    assert listed["stale_stages"] == []
    assert run["result"]["script"]["shots"][0]["shot_id"] == "SH001"
    assert run["result"]["shots"] == run["result"]["script"]["shots"]


def test_active_script_run_blocks_a_second_submit(service, manual):
    _, calls, _, submit = manual
    first = submit("script", key="once")
    assert submit("script", key="once") == first
    with pytest.raises(AppError, match="执行中的脚本任务"):
        submit("script", key="concurrent")
    process(service, first)
    assert submit("script", key="after") != first
    assert calls == ["script"]


def test_confirmed_script_is_marked_and_revision_checked(service, manual):
    _, calls, _, submit = manual
    run = process(service, submit("script"))
    service.confirm_stage(run["id"], "script", run["revision"])
    confirmed = service.get_run(run["id"])
    assert confirmed["revision"] == run["revision"] + 1
    assert "script" in confirmed["result"]["confirmed_stages"]
    with pytest.raises(AppError, match="结果已更新"):
        service.confirm_stage(run["id"], "script", run["revision"])
    with pytest.raises(AppError, match="阶段无效"):
        service.confirm_stage(confirmed["id"], "analysis", confirmed["revision"])
    assert calls == ["script"]


def test_manual_edit_saves_a_new_version_and_flags_review(service, manual):
    _, calls, models, submit = manual
    run = process(service, submit("script"))
    edited = deepcopy(run["result"]["script"])
    edited["shots"][0]["dialogue"] = "少年：“有人吗？”"
    edited["shots"][0]["duration_seconds"] = 8
    service.save_run_script(run["id"], edited, run["revision"])
    saved = service.get_run(run["id"])
    assert saved["revision"] == run["revision"] + 1
    assert saved["status"] == "review_required"
    assert saved["result"]["script"]["shots"][0]["dialogue"] == "少年：“有人吗？”"
    assert saved["result"]["shots"] == saved["result"]["script"]["shots"]
    assert "手工修改后需要复核画面连贯与台词还原。" in saved["validation_issues"]
    with pytest.raises(AppError, match="脚本已更新"):
        service.save_run_script(run["id"], edited, run["revision"])
    broken = deepcopy(edited)
    broken["shots"][0]["shot_size"] = "大远景"
    with pytest.raises(AppError, match="脚本行格式不正确"):
        service.save_run_script(run["id"], broken, saved["revision"])
    assert service.get_run(run["id"])["revision"] == saved["revision"]
    assert calls == ["script"]


def test_edit_stage_accepts_only_the_story_script(service, manual):
    _, calls, models, submit = manual
    run = process(service, submit("script"))
    edited = deepcopy(run["result"]["script"])
    edited["shots"][0]["action"] = "少年用力推开殿门"
    ident = service.edit_stage(run["id"], "script", run["revision"], edited)
    current = service.get_run(ident)
    assert current["status"] in {"succeeded", "review_required"}
    assert current["result"]["script"]["shots"][0]["action"] == "少年用力推开殿门"
    with pytest.raises(AppError, match="仅支持在此编辑故事脚本"):
        service.edit_stage(run["id"], "analysis", run["revision"], edited)
    historical = next(r for r in service.list_runs(run["chapter_id"]) if r["id"] == run["id"])
    assert historical["result"]["script"] == run["result"]["script"]
    assert calls == ["script"]


def test_changed_chapter_marks_old_script_stale_and_blocks_edit(service, manual):
    chapter, calls, models, submit = manual
    run = process(service, submit("script"))
    old = service.get_chapter(chapter)
    service.save_chapter(old["work_id"], "第一章", "少年走出殿门。", "", chapter, old["revision"])
    stale = service.list_runs(chapter)[0]
    assert stale["stale_stages"] == ["script"]
    with pytest.raises(AppError, match="依据已更新"):
        service.edit_stage(run["id"], "script", run["revision"], run["result"]["script"])
    with pytest.raises(AppError, match="章节已更新"):
        service.save_run_script(run["id"], run["result"]["script"], run["revision"])
    with pytest.raises(AppError, match="依据已更新"):
        service.confirm_stage(run["id"], "script", run["revision"])
    restarted = process(service, submit("script", run))
    assert restarted["document"]["text"] == "少年走出殿门。"
    assert calls == ["script", "script"]


def test_retry_continues_the_saved_script_stage(service, manual, monkeypatch):
    chapter, calls, models, submit = manual
    run = submit("script")
    with service.sessions.begin() as session:
        row = session.get(RunRow, run)
        row.document = {key: value for key, value in row.document.items() if key != "target_stage"}
        row.result = {"paragraphs": [{"paragraph_id": "P0001", "text": "少年推开殿门。"}]}
        row.status = "failed"
        row.stage = "script"
    service.retry_run(run, stage_only=True)
    result = process(service, run)
    assert calls == ["script"]
    assert result["status"] == "succeeded"
    assert result["result"]["script"]["shots"][0]["shot_id"] == "SH001"
    assert result["stage"] == "script"


def test_generated_images_survive_script_regeneration(service, manual):
    chapter, calls, models, submit = manual
    run = process(service, submit("script"))
    shot_id = run["result"]["shots"][0]["shot_id"]
    source = service.shot_image_source(run["id"], shot_id)
    service.save_shot_images(
        run["id"],
        shot_id,
        source["source_hash"],
        [
            {
                "asset_id": "existing-image",
                "filename": "existing.png",
                "mime_type": "image/png",
                "width": 1,
                "height": 1,
                "prompt": "原有提示词",
                "model": "fake",
            }
        ],
    )
    service.select_shot_image(run["id"], shot_id, "existing-image")
    regenerated = process(service, submit("script", run))
    images = service.shot_image_source(regenerated["id"], shot_id)["images"]
    assert len(images) == 1
    assert images[0]["asset_id"] == "existing-image"
    assert images[0]["selected"]
    assert calls == ["script", "script"]


def test_api_defaults_to_the_script_stage(service, manual, monkeypatch):
    from novel_anime.ui.web_server import dispatch

    chapter, calls, models, _ = manual
    env = "TEST_SCRIPT_KEY"
    profile = ModelProfile(
        provider="test",
        protocol="chat_completions",
        base_url="https://example.com/v1",
        model="fake",
        api_key_env=env,
    ).model_dump()
    service.save_profile(profile)
    monkeypatch.setenv(env, "mock-only-key")
    result = dispatch(
        service,
        "run",
        {
            "chapter_id": chapter,
            "profile_id": profile["profile_id"],
            "idempotency_key": "api-script",
        },
    )
    run = process(service, result["id"])
    assert calls == ["script"]
    assert run["status"] == "succeeded"
    dispatch(
        service,
        "save-script",
        {"run_id": run["id"], "revision": run["revision"], "script": run["result"]["script"]},
    )
    assert service.get_run(run["id"])["status"] == "review_required"
