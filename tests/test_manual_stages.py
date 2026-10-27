"""Manual phase boundaries, confirmations and immutable version lineage."""

from copy import deepcopy

import pytest
import test_service
from test_models import storyboard

from novel_anime.core.errors import AppError
from novel_anime.domain.models import ModelProfile, Review, ShotBatch
from novel_anime.integrations.language_models.client import LLMClient
from novel_anime.persistence.database import RunRow
from novel_anime.workers.main import Worker


@pytest.fixture
def service(monkeypatch, tmp_path):
    return test_service.service.__wrapped__(monkeypatch, tmp_path)


@pytest.fixture
def manual(service, monkeypatch):
    analysis, outline, shots, _ = storyboard()
    models = {
        "analysis": analysis,
        "outline": outline,
        "shots": ShotBatch(shots=shots),
        "review": Review(),
    }
    calls = []

    def generate(client, schema, system, payload, stage):
        calls.append(stage)
        return models[stage], {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    work = service.create_work("分步作品")
    chapter = service.save_chapter(work, "第一章", "少年推开殿门。", "")
    profile = ModelProfile(
        provider="test",
        protocol="chat_completions",
        base_url="https://example.com/v1",
        model="fake",
        api_key_env="",
    ).model_dump()

    def submit(stage, source=None, key=None, selected=None):
        return service.create_run(
            chapter,
            profile,
            "二维动画",
            "16:9",
            "",
            key or stage + "-" + str(len(calls)),
            target_stage=stage,
            source_run_id=source and source["id"],
            source_revision=source and source["revision"],
            selected_stages=selected,
        )

    return chapter, calls, models, submit


def process(service, run):
    worker = Worker()
    assert worker.claim() == run
    worker.process(run)
    assert service.get_run(run)["owner"] is None
    return service.get_run(run)


def approve(service, run, stage):
    service.confirm_stage(run["id"], stage, run["revision"])
    return service.get_run(run["id"])


def test_only_one_stage_runs_and_next_requires_confirmation(service, manual):
    chapter, calls, models, submit = manual
    analysis = process(service, submit("analysis"))
    assert calls == ["analysis"]
    assert analysis["status"] == "stage_complete"
    assert "outline" not in analysis["result"]
    with pytest.raises(AppError, match="确认"):
        submit("outline", analysis)
    analysis = approve(service, analysis, "analysis")
    outline = process(service, submit("outline", analysis))
    assert calls == ["analysis", "outline"]
    assert outline["result"]["analysis"] == analysis["result"]["analysis"]
    assert "shots" not in outline["result"]
    outline = approve(service, outline, "outline")
    shots = process(service, submit("shots", outline))
    assert calls == ["analysis", "outline", "shots"]
    assert "review" not in shots["result"]
    shots = approve(service, shots, "shots")
    review = process(service, submit("review", shots))
    assert calls == ["analysis", "outline", "shots", "review"]
    assert review["status"] == "succeeded"
    assert service.get_run(shots["id"])["result"]["shots"] == review["result"]["shots"]
    assert service.list_runs(chapter)[0]["stale_stages"] == []


def test_selected_adjacent_stages_run_together_and_stop(service, manual):
    _, calls, _, submit = manual
    run = process(service, submit("analysis", selected=["analysis", "outline"]))
    assert calls == ["analysis", "outline"]
    assert run["status"] == "stage_complete"
    assert "shots" not in run["result"]
    assert "analysis" in run["result"]["confirmed_stages"]
    assert "outline" not in run["result"]["confirmed_stages"]
    with pytest.raises(AppError, match="确认"):
        submit("shots", run)


@pytest.mark.parametrize(
    "selected", [[], ["analysis", "shots"], ["shots", "analysis"], ["analysis", "analysis"]]
)
def test_reject_nonadjacent_stage_selection(service, manual, selected):
    _, calls, _, submit = manual
    with pytest.raises(AppError):
        submit("analysis", selected=selected)
    assert calls == []


def test_combined_failure_keeps_success_and_retries_remaining(service, manual, monkeypatch):
    _, calls, models, submit = manual

    def fail_outline(client, schema, system, payload, stage):
        calls.append(stage)
        if stage == "outline":
            raise AppError("llm_connection", "模拟断连")
        return models[stage], {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", fail_outline)
    run = process(service, submit("analysis", selected=["analysis", "outline", "shots"]))
    assert run["status"] == "failed"
    assert run["result"]["analysis"]
    assert "shots" not in run["result"]

    def fixed(client, schema, system, payload, stage):
        calls.append(stage)
        return models[stage], {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", fixed)
    service.retry_run(run["id"], stage_only=True)
    retried = process(service, run["id"])
    assert calls == ["analysis", "outline", "outline", "shots"]
    assert retried["status"] == "stage_complete"
    assert "review" not in retried["result"]


def test_combined_through_review_finishes(service, manual):
    _, calls, _, submit = manual
    run = process(service, submit("analysis", selected=["analysis", "outline", "shots", "review"]))
    assert calls == ["analysis", "outline", "shots", "review"]
    assert run["status"] == "succeeded"


def test_stop_during_model_call_saves_result_and_skips_next_stage(service, manual, monkeypatch):
    _, calls, models, submit = manual
    run_id = submit("analysis", selected=["analysis", "outline"])

    def generate(client, schema, system, payload, stage):
        calls.append(stage)
        service.stop_run(run_id)
        return models[stage], {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    run = process(service, run_id)
    assert calls == ["analysis"]
    assert run["status"] == "stopped"
    assert run["result"]["analysis"]
    assert "outline" not in run["result"]


def test_failed_stage_retries_only_itself(service, manual, monkeypatch):
    chapter, calls, models, submit = manual
    analysis = approve(service, process(service, submit("analysis")), "analysis")

    def broken(*args):
        raise AppError("llm_connection", "模拟断连")

    monkeypatch.setattr(LLMClient, "generate", broken)
    outline = process(service, submit("outline", analysis))
    assert outline["status"] == "failed"
    assert outline["result"]["analysis"] == analysis["result"]["analysis"]

    def fixed(client, schema, system, payload, stage):
        calls.append(stage)
        assert stage == "outline"
        return models[stage], {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", fixed)
    service.retry_run(outline["id"], stage_only=True)
    result = process(service, outline["id"])
    assert result["status"] == "stage_complete"
    assert calls == ["analysis", "outline"]
    assert "shots" not in result["result"]


def test_upstream_regeneration_retains_but_marks_old_downstream_stale(service, manual):
    chapter, calls, models, submit = manual
    analysis = approve(service, process(service, submit("analysis")), "analysis")
    outline = approve(service, process(service, submit("outline", analysis)), "outline")
    edited = deepcopy(outline["result"]["analysis"])
    edited["summary"] = "用户纠正后的分析"
    new_id = service.edit_stage(outline["id"], "analysis", outline["revision"], edited)
    current = service.get_run(new_id)
    assert current["status"] == "stage_complete"
    assert "outline" not in current["result"]
    historical = next(r for r in service.list_runs(chapter) if r["id"] == outline["id"])
    assert historical["result"]["outline"] == outline["result"]["outline"]
    assert "outline" in historical["stale_stages"]
    assert "analysis" in historical["stale_stages"]
    with pytest.raises(AppError):
        submit("outline", current)
    with pytest.raises(AppError):
        approve(service, outline, "outline")
    current = approve(service, current, "analysis")
    regenerated = process(service, submit("outline", current))
    assert regenerated["result"]["analysis"]["summary"] == "用户纠正后的分析"


def test_changed_chapter_blocks_continuation_and_keeps_old_results(service, manual):
    chapter, calls, models, submit = manual
    analysis = approve(service, process(service, submit("analysis")), "analysis")
    old = service.get_chapter(chapter)
    service.save_chapter(old["work_id"], "第一章", "少年走出殿门。", "", chapter, old["revision"])
    assert "analysis" in service.list_runs(chapter)[0]["stale_stages"]
    with pytest.raises(AppError):
        submit("outline", analysis)
    with pytest.raises(AppError):
        service.edit_stage(
            analysis["id"], "analysis", analysis["revision"], analysis["result"]["analysis"]
        )
    restarted = process(service, submit("analysis", analysis))
    assert restarted["document"]["text"] == "少年走出殿门。"
    assert calls == ["analysis", "analysis"]


def test_duplicate_submit_and_stale_revision_do_not_start_extra_requests(service, manual):
    chapter, calls, models, submit = manual
    run = submit("analysis", key="once")
    assert submit("analysis", key="once") == run
    with pytest.raises(AppError):
        submit("analysis", key="concurrent")
    result = process(service, run)
    approved = approve(service, result, "analysis")
    with pytest.raises(AppError):
        submit("outline", result)
    assert submit("outline", approved)
    assert calls == ["analysis"]


def test_legacy_failed_run_continues_only_its_current_stage(service, manual, monkeypatch):
    chapter, calls, models, submit = manual
    run = submit("analysis")
    with service.sessions.begin() as session:
        row = session.get(RunRow, run)
        row.document = {key: value for key, value in row.document.items() if key != "target_stage"}
        row.result = {
            "analysis": models["analysis"].model_dump(),
            "paragraphs": [p.model_dump() for p in storyboard()[3]],
        }
        row.status = "failed"
        row.stage = "outline"
    service.retry_run(run, stage_only=True)
    result = process(service, run)
    assert calls == ["outline"]
    assert result["status"] == "stage_complete"
    assert "shots" not in result["result"]


def test_separate_review_preserves_existing_shot_images(service, manual):
    chapter, calls, models, submit = manual
    analysis = approve(service, process(service, submit("analysis")), "analysis")
    outline = approve(service, process(service, submit("outline", analysis)), "outline")
    shots = approve(service, process(service, submit("shots", outline)), "shots")
    shot = models["shots"].shots[0]
    source = service.shot_image_source(shots["id"], shot.shot_id)
    service.save_shot_images(
        shots["id"],
        shot.shot_id,
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
    service.select_shot_image(shots["id"], shot.shot_id, "existing-image")
    review = process(service, submit("review", shots))
    images = service.shot_image_source(review["id"], shot.shot_id)["images"]
    assert len(images) == 1
    assert images[0]["asset_id"] == "existing-image"
    assert images[0]["selected"]


def test_api_defaults_to_single_analysis(service, manual, monkeypatch):
    from novel_anime.ui.web_server import dispatch

    chapter, calls, models, _ = manual
    profile = ModelProfile(
        provider="test",
        protocol="chat_completions",
        base_url="https://example.com/v1",
        model="fake",
    ).model_dump()
    service.save_profile(profile)
    monkeypatch.setenv(profile["api_key_env"], "mock-only-key")
    result = dispatch(
        service,
        "run",
        {
            "chapter_id": chapter,
            "profile_id": profile["profile_id"],
            "idempotency_key": "api-analysis",
        },
    )
    run = process(service, result["id"])
    assert calls == ["analysis"]
    assert run["status"] == "stage_complete"
    dispatch(
        service,
        "confirm-stage",
        {"run_id": run["id"], "stage": "analysis", "revision": run["revision"]},
    )
    assert service.get_run(run["id"])["result"]["confirmed_stages"]["analysis"]
