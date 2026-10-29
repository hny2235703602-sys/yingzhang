"""Story-script workflow: one model stage, resumable batches, budget guards."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import test_service
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel
from test_service import create_run

from novel_anime.application.service import StudioService
from novel_anime.core.errors import AppError
from novel_anime.domain.models import (
    InputDocument,
    ModelProfile,
    ScriptBatch,
    ScriptCharacter,
    ScriptRow,
)
from novel_anime.integrations.language_models.client import LLMClient
from novel_anime.workers.main import Worker
from novel_anime.workflows.adaptation import AdaptationFlow, paragraphs


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> StudioService:
    fixture: Any = test_service.service
    return fixture.__wrapped__(monkeypatch, tmp_path)


def batch(
    *row_updates: dict[str, Any], characters: list[ScriptCharacter] | None = None
) -> ScriptBatch:
    defaults: dict[str, Any] = {
        "duration_seconds": 5,
        "shot_size": "中景",
        "camera": "缓慢推进",
        "frame": "少年双手推在殿门板上",
        "action": "少年推开殿门",
        "dialogue": "",
        "character_ids": ["hero"],
        "setting": "殿门外，雨夜",
        "source_paragraph_ids": ["P0001"],
    }
    rows = [ScriptRow(**{**defaults, **update}) for update in (row_updates or ({},))]
    return ScriptBatch(
        characters=characters
        if characters is not None
        else [ScriptCharacter(character_id="hero", name="少年", appearance="黑发")],
        shots=rows,
    )


def profile() -> ModelProfile:
    return ModelProfile(
        provider="local",
        protocol="chat_completions",
        base_url="http://localhost:1234/v1",
        model="fake",
        api_key_env="",
    )


def test_paragraph_numbering_preserves_dialogue_and_meaningful_repetition() -> None:
    result = paragraphs("  “别动！”\r\n\r\n他停住。\n他停住。  ")
    assert [p.text for p in result] == ["“别动！”", "他停住。", "他停住。"]
    assert len({p.paragraph_id for p in result}) == 3
    assert result[0].paragraph_id == "P0001"


def test_full_graph_runs_only_the_script_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[tuple[dict[str, Any], str]] = []
    calls: list[str] = []
    boundaries: list[str] = []
    document = InputDocument(work_id="work", title="章节", text="少年推开殿门。").model_dump()

    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        calls.append(stage)
        assert payload["paragraphs"][0]["paragraph_id"] == "P0001"
        return batch(), {"stage": stage, "usage": None}

    monkeypatch.setattr(LLMClient, "generate", generate)
    flow = AdaptationFlow(
        document,
        profile(),
        lambda result, stage: saved.append((deepcopy(result), stage)),
        boundaries.append,
    )
    result = flow.run({}, InMemorySaver(), "run-1")
    assert calls == ["script"]
    assert boundaries == ["script"]
    assert result["schema_version"] == "2.0"
    assert result["prompt_template_version"] == "2.0"
    assert result["validation_issues"] == []
    assert len(result["provenance"]) == 1
    assert [shot["shot_id"] for shot in result["script"]["shots"]] == ["SH001"]
    assert result["shots"] == result["script"]["shots"]
    assert result["script"]["shots"][0]["order"] == 1
    assert "script_rows" not in result and "script_covered" not in result
    # Recreate the complete flow and checkpoint backend from its durable business result.
    calls.clear()
    resumed = AdaptationFlow(document, profile(), lambda result, stage: None, boundaries.append)
    assert resumed.run(result, InMemorySaver(), "run-1")["shots"] == result["shots"]
    assert calls == []


def test_saved_batches_survive_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[list[str]] = []
    document = InputDocument(
        work_id="work", title="章节", text="甲走进庭院。\n\n乙停下脚步。"
    ).model_dump()
    initial = {
        "paragraphs": [
            {"paragraph_id": "P0001", "text": "甲走进庭院。"},
            {"paragraph_id": "P0002", "text": "乙停下脚步。"},
        ],
        "script_rows": [
            {
                "shot_id": "SH001",
                "order": 1,
                "duration_seconds": 5,
                "shot_size": "全景",
                "camera": "固定",
                "frame": "甲走进庭院",
                "action": "甲走进庭院",
                "dialogue": "",
                "character_ids": [],
                "setting": "",
                "source_paragraph_ids": ["P0001"],
                "generation_status": "not_submitted",
            }
        ],
        "script_covered": ["P0001"],
        "script_characters": [],
        "provenance": [],
    }

    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        sent.append([item["paragraph_id"] for item in payload["paragraphs"]])
        assert payload["previous_rows"][0]["shot_id"] == "SH001"
        return ScriptBatch(
            shots=[
                ScriptRow(
                    duration_seconds=4,
                    shot_size="近景",
                    camera="跟拍",
                    frame="乙停在门边",
                    action="乙停下",
                    source_paragraph_ids=["P0002"],
                )
            ]
        ), {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    flow = AdaptationFlow(document, profile(), lambda result, stage: None, lambda stage: None)
    result = flow.run(initial, InMemorySaver(), "partial-run")
    assert sent == [["P0002"]]
    assert [shot["shot_id"] for shot in result["shots"]] == ["SH001", "SH002"]
    assert [shot["order"] for shot in result["shots"]] == [1, 2]
    assert result["validation_issues"] == []


def test_prompt_template_override_comes_from_the_document(monkeypatch: pytest.MonkeyPatch) -> None:
    used: list[str] = []

    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        used.append(system)
        return batch(), {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    document = InputDocument(work_id="work", title="章节", text="少年推开殿门。").model_dump()
    document["prompt_templates"] = {"script": "自定义故事脚本提示词"}
    flow = AdaptationFlow(document, profile(), lambda result, stage: None, lambda stage: None)
    flow.run({}, InMemorySaver(), "custom-prompt")
    assert used == ["自定义故事脚本提示词"]


def test_single_paragraph_over_budget_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        raise AssertionError("over-budget text must not be sent")

    monkeypatch.setattr(LLMClient, "generate", generate)
    document = InputDocument(work_id="work", title="章节", text="甲" * 30000).model_dump()
    flow = AdaptationFlow(
        document,
        ModelProfile(
            provider="local",
            protocol="chat_completions",
            base_url="http://localhost:1234/v1",
            model="fake",
            api_key_env="",
            context_budget=16000,
            max_output_tokens=4000,
        ),
        lambda result, stage: None,
        lambda stage: None,
    )
    with pytest.raises(AppError) as caught:
        flow.run({}, InMemorySaver(), "over-budget")
    assert caught.value.code == "paragraph_too_long"


def test_short_dialogue_chapter_fits_budget_in_bounded_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = [f"「第{i:03d}句」他应了一声，目光没有离开门口。" for i in range(160)]
    text = "\n".join(lines)
    assert len(text) > 3000
    calls: list[str] = []

    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        calls.append(stage)
        return batch(
            *[
                {
                    "source_paragraph_ids": [
                        payload["paragraphs"][0]["paragraph_id"],
                    ]
                }
            ]
        ), {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    document = InputDocument(work_id="work", title="章节", text=text).model_dump()
    flow = AdaptationFlow(document, profile(), lambda result, stage: None, lambda stage: None)
    result = flow.run({}, InMemorySaver(), "dialogue-run")
    assert len(calls) > 1 and set(calls) == {"script"}
    assert "script" in result


def test_empty_model_output_keeps_saved_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        calls.append(stage)
        return ScriptBatch(), {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    document = InputDocument(work_id="work", title="章节", text="少年推开殿门。").model_dump()
    saved: list[dict[str, Any]] = []
    flow = AdaptationFlow(
        document,
        profile(),
        lambda result, stage: saved.append(deepcopy(result)),
        lambda stage: None,
    )
    with pytest.raises(AppError) as caught:
        flow.run({}, InMemorySaver(), "empty-script")
    assert caught.value.code == "empty_script"
    assert saved and saved[-1]["paragraphs"][0]["paragraph_id"] == "P0001"


def test_worker_restart_retains_paragraphs_and_does_not_repeat_model_call(
    service: StudioService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    fail = True

    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        calls.append(stage)
        if fail:
            raise AppError("llm_unavailable", "模拟服务不可用")
        return batch(), {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    run_id = create_run(service)
    first = Worker()
    assert first.claim() == run_id
    first.process(run_id)
    interrupted = service.get_run(run_id)
    assert interrupted["status"] == "failed"
    assert interrupted["result"]["paragraphs"][0]["paragraph_id"] == "P0001"
    assert "script" not in interrupted["result"]
    assert interrupted["owner"] is None
    fail = False
    service.retry_run(run_id)
    restarted = Worker()
    assert restarted.claim() == run_id
    restarted.process(run_id)
    complete = service.get_run(run_id)
    assert complete["status"] == "succeeded"
    assert calls == ["script", "script"]
    assert complete["owner"] is None
    assert complete["lease_until"] is None
    assert complete["validation_issues"] == []


def test_pause_at_boundary_then_resume_without_repeating_script(
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
        if pause_once:
            pause_once = False
            service.pause_run(run_id)
        return batch(), {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    worker = Worker()
    assert worker.claim() == run_id
    worker.process(run_id)
    paused = service.get_run(run_id)
    assert paused["status"] == "paused"
    assert paused["owner"] is None
    assert paused["result"]["script"]["shots"]
    service.resume_run(run_id)
    second = Worker()
    assert second.claim() == run_id
    second.process(run_id)
    assert service.get_run(run_id)["status"] == "succeeded"
    assert calls == ["script"]


def test_validation_issues_require_review_instead_of_success(
    service: StudioService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = service.create_work("两段作品")
    chapter_id = service.save_chapter(work, "第一章", "少年推门。\n\n少女走来。", "")
    run_id = service.create_run(
        chapter_id,
        profile().model_dump(),
        "二维动画",
        "16:9",
        "",
        "review-run",
    )

    def generate(
        self: LLMClient, schema: type[BaseModel], system: str, payload: dict[str, Any], stage: str
    ) -> tuple[BaseModel, dict[str, Any]]:
        return batch(), {"stage": stage}

    monkeypatch.setattr(LLMClient, "generate", generate)
    worker = Worker()
    assert worker.claim() == run_id
    worker.process(run_id)
    row = service.get_run(run_id)
    assert row["status"] == "review_required"
    assert any("未被写入脚本" in issue for issue in row["validation_issues"])


def test_unlimited_budget_still_splits_long_chapter():
    flow = AdaptationFlow(
        InputDocument(work_id="work", title="章节", text="正文").model_dump(),
        profile().model_copy(
            update={
                "context_budget": 0,
                "max_output_tokens": 0,
            }
        ),
        lambda *args: None,
        lambda *args: None,
    )
    source = [{"paragraph_id": f"P{i:04d}", "text": "文" * 400} for i in range(1, 8)]
    chunks = flow._chunks(source)
    assert len(chunks) == 4
    assert [p for chunk in chunks for p in chunk] == source
    assert all(sum(len(p["text"]) for p in chunk) <= 1000 for chunk in chunks)
