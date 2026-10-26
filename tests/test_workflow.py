from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import test_service
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel
from test_models import storyboard
from test_service import create_run

from novel_anime.application.service import StudioService
from novel_anime.core.errors import AppError
from novel_anime.domain.models import InputDocument, ModelProfile, Review, ShotBatch
from novel_anime.integrations.language_models.client import LLMClient
from novel_anime.workers.main import Worker
from novel_anime.workflows.adaptation import AdaptationFlow, paragraphs


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> StudioService:
    fixture: Any = test_service.service
    return fixture.__wrapped__(monkeypatch, tmp_path)


def fake_models() -> dict[str, BaseModel]:
    analysis, outline, shots, _ = storyboard()
    return {
        "analysis": analysis,
        "outline": outline,
        "shots": ShotBatch(shots=shots),
        "review": Review(),
    }


def test_paragraph_numbering_preserves_dialogue_and_meaningful_repetition() -> None:
    result = paragraphs("  “别动！”\r\n\r\n他停住。\n他停住。  ")
    assert [p.text for p in result] == ["“别动！”", "他停住。", "他停住。"]
    assert len({p.paragraph_id for p in result}) == 3
    assert result[0].paragraph_id == "P0001"


def test_full_graph_and_restart_skip_saved_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[tuple[dict[str, Any], str]] = []
    calls: list[str] = []
    boundaries: list[str] = []
    document = InputDocument(work_id="work", title="章节", text="少年推开殿门。").model_dump()
    profile = ModelProfile(
        provider="local",
        protocol="chat_completions",
        base_url="http://localhost:1234/v1",
        model="fake",
        api_key_env="",
    )

    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        calls.append(stage)
        return fake_models()[stage], {"stage": stage, "usage": None}

    monkeypatch.setattr(LLMClient, "generate", generate)
    flow = AdaptationFlow(
        document,
        profile,
        lambda result, stage: saved.append((deepcopy(result), stage)),
        boundaries.append,
    )
    result = flow.run({}, InMemorySaver(), "run-1")
    assert calls == ["analysis", "outline", "shots", "review"]
    assert boundaries == calls
    assert result["validation_issues"] == []
    assert result["generation_status"] == "not_submitted"
    assert len(result["provenance"]) == 4
    # Recreate the complete flow and checkpoint backend from its durable business result.
    calls.clear()
    resumed = AdaptationFlow(document, profile, lambda result, stage: None, boundaries.append)
    assert resumed.run(result, InMemorySaver(), "run-1")["shots"] == result["shots"]
    assert calls == []


def test_partial_scene_batches_survive_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    models = fake_models()
    outline = models["outline"].model_dump()
    second_scene = deepcopy(outline["scenes"][0])
    second_scene["scene_id"] = "SC002"
    outline["scenes"].append(second_scene)
    analysis, _, first_shots, source = storyboard()
    initial = {
        "paragraphs": [paragraph.model_dump() for paragraph in source],
        "analysis": analysis.model_dump(),
        "outline": outline,
        "shot_batches": {"SC001": [shot.model_dump() for shot in first_shots]},
        "provenance": [],
    }
    stages: list[tuple[str, str]] = []

    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        stages.append((stage, payload["scene"]["scene_id"]))
        if stage == "shots":
            assert payload["previous_shot"]["shot_id"] == "SH001"
            second = first_shots[0].model_copy(
                update={
                    "shot_id": "SC002_SH001",
                    "scene_id": "SC002",
                    "order": 2,
                    "depends_on_shot_ids": ["SH001"],
                }
            )
            return ShotBatch(shots=[second]), {"stage": stage}
        return Review(), {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    document = InputDocument(work_id="work", title="章节", text="少年推开殿门。").model_dump()
    profile = ModelProfile(
        provider="local",
        protocol="chat_completions",
        base_url="http://localhost:1234/v1",
        model="fake",
        api_key_env="",
    )
    flow = AdaptationFlow(document, profile, lambda result, stage: None, lambda stage: None)
    complete = flow.run(initial, InMemorySaver(), "partial-run")
    assert stages == [("shots", "SC002"), ("review", "SC001"), ("review", "SC002")]
    assert [shot["order"] for shot in complete["shots"]] == [1, 2]
    assert complete["validation_issues"] == []


def test_worker_restart_retains_analysis_and_does_not_repeat_model_call(
    service: StudioService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    fail_outline = True

    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        calls.append(stage)
        if stage == "outline" and fail_outline:
            raise AppError("llm_unavailable", "模拟服务不可用")
        return fake_models()[stage], {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    run_id = create_run(service)
    first = Worker()
    assert first.claim() == run_id
    first.process(run_id)
    interrupted = service.get_run(run_id)
    assert interrupted["status"] == "failed"
    assert "analysis" in interrupted["result"]
    assert interrupted["owner"] is None
    fail_outline = False
    service.retry_run(run_id)
    restarted = Worker()
    assert restarted.claim() == run_id
    restarted.process(run_id)
    complete = service.get_run(run_id)
    assert complete["status"] == "succeeded"
    assert calls == ["analysis", "outline", "outline", "shots", "review"]
    assert complete["owner"] is None
    assert complete["lease_until"] is None
    assert complete["validation_issues"] == []


def test_pause_at_boundary_then_resume_without_repeating_analysis(
    service: StudioService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    run_id = create_run(service)
    pause_once = True

    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        nonlocal pause_once
        calls.append(stage)
        if stage == "analysis" and pause_once:
            pause_once = False
            service.pause_run(run_id)
        return fake_models()[stage], {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    worker = Worker()
    assert worker.claim() == run_id
    worker.process(run_id)
    paused = service.get_run(run_id)
    assert paused["status"] == "paused"
    assert paused["owner"] is None
    assert "analysis" in paused["result"]
    assert "outline" not in paused["result"]
    service.resume_run(run_id)
    second = Worker()
    assert second.claim() == run_id
    second.process(run_id)
    assert service.get_run(run_id)["status"] == "succeeded"
    assert calls == ["analysis", "outline", "shots", "review"]


def test_short_dialogue_chapter_fits_default_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [f"「第{i:03d}句」他应了一声，目光没有离开门口。" for i in range(160)]
    text = "\n".join(lines)
    assert len(text) > 3000
    calls: list[str] = []

    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        calls.append(stage)
        return fake_models()[stage], {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    document = InputDocument(work_id="work", title="章节", text=text).model_dump()
    profile = ModelProfile(
        provider="local",
        protocol="chat_completions",
        base_url="http://localhost:1234/v1",
        model="fake",
        api_key_env="",
    )
    flow = AdaptationFlow(document, profile, lambda result, stage: None, lambda stage: None)
    result = flow.run({}, InMemorySaver(), "dialogue-run")
    assert calls == ["analysis", "outline", "shots", "review"]
    assert "analysis" in result


def test_single_paragraph_over_budget_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        raise AssertionError("over-budget text must not be sent")

    monkeypatch.setattr(LLMClient, "generate", generate)
    document = InputDocument(work_id="work", title="章节", text="甲" * 30000).model_dump()
    profile = ModelProfile(
        provider="local",
        protocol="chat_completions",
        base_url="http://localhost:1234/v1",
        model="fake",
        api_key_env="",
        context_budget=16000,
        max_output_tokens=4000,
    )
    flow = AdaptationFlow(document, profile, lambda result, stage: None, lambda stage: None)
    with pytest.raises(AppError) as caught:
        flow.run({}, InMemorySaver(), "over-budget")
    assert caught.value.code == "paragraph_too_long"


def test_semantic_issues_require_review_instead_of_success(
    service: StudioService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        model = (
            Review(issues=["SH001 依赖未生成的对白"]) if stage == "review" else fake_models()[stage]
        )
        return model, {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    run_id = create_run(service)
    worker = Worker()
    assert worker.claim() == run_id
    worker.process(run_id)
    row = service.get_run(run_id)
    assert row["status"] == "review_required"
    assert row["validation_issues"] == ["SH001 依赖未生成的对白"]
