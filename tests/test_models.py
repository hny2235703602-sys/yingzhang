from copy import deepcopy

import pytest
from pydantic import ValidationError

from novel_anime.domain.models import (
    Analysis,
    Character,
    InputDocument,
    ModelProfile,
    Outline,
    Scene,
    Shot,
    SourceParagraph,
    StoryEvent,
)
from novel_anime.domain.validation import validate_storyboard


def storyboard() -> tuple[Analysis, Outline, list[Shot], list[SourceParagraph]]:
    paragraphs = [SourceParagraph(paragraph_id="P0001", text="少年推开殿门。")]
    analysis = Analysis(
        summary="少年入殿",
        characters=[
            Character(
                character_id="hero",
                name="少年",
                description="推门者",
                source_paragraph_ids=["P0001"],
            )
        ],
        events=[
            StoryEvent(
                event_id="EV001",
                description="推开殿门",
                importance="must",
                source_paragraph_ids=["P0001"],
            )
        ],
    )
    outline = Outline(
        synopsis="入殿",
        scenes=[
            Scene(
                scene_id="SC001",
                title="殿门",
                location="殿外",
                time="白日",
                script="少年推门",
                event_ids=["EV001"],
            )
        ],
    )
    shots = [
        Shot(
            shot_id="SH001",
            scene_id="SC001",
            order=1,
            title="推门",
            source_paragraph_ids=["P0001"],
            source_event_ids=["EV001"],
            character_ids=["hero"],
            story_purpose="进入大殿",
            action="推门",
            shot_size="中景",
            camera="固定",
            start_state="门关",
            end_state="门开",
            video_prompt="少年在殿门外推开门，固定镜头。",
        )
    ]
    return analysis, outline, shots, paragraphs


def test_valid_storyboard() -> None:
    assert validate_storyboard(*storyboard()) == []


def test_contract_forbids_extra_and_empty_required_fields() -> None:
    with pytest.raises(ValidationError):
        InputDocument(work_id="w", title="t", text=" ")
    with pytest.raises(ValidationError):
        Analysis.model_validate({"summary": "概述", "invented": True})
    with pytest.raises(ValidationError):
        StoryEvent(event_id="e", description="x", importance="essential")
    with pytest.raises(ValidationError):
        SourceParagraph(paragraph_id="../../bad", text="x")


def test_mutable_defaults_are_independent() -> None:
    first = Analysis(summary="a")
    second = Analysis(summary="b")
    first.uncertainties.append("未知")
    assert second.uncertainties == []


def test_profile_budget() -> None:
    with pytest.raises(ValidationError):
        ModelProfile(
            provider="p",
            protocol="responses",
            base_url="http://localhost/v1",
            model="m",
            context_budget=100,
            max_output_tokens=100,
        )


def test_missing_references_and_coverage() -> None:
    analysis, outline, shots, paragraphs = storyboard()
    shots[0].source_event_ids = ["missing"]
    shots[0].character_ids = ["missing"]
    shots[0].source_paragraph_ids = ["missing"]
    outline.scenes[0].event_ids = []
    issues = validate_storyboard(analysis, outline, shots, paragraphs)
    assert any("原文引用不存在" in issue for issue in issues)
    assert any("角色引用不存在" in issue for issue in issues)
    assert any("未被镜头覆盖" in issue for issue in issues)
    assert any("未被大纲覆盖" in issue for issue in issues)


def test_duplicate_order_and_dependency_cycle() -> None:
    analysis, outline, shots, paragraphs = storyboard()
    second = deepcopy(shots[0])
    second.shot_id = "SH002"
    second.depends_on_shot_ids = ["SH001"]
    shots[0].depends_on_shot_ids = ["SH002"]
    shots.append(second)
    issues = validate_storyboard(analysis, outline, shots, paragraphs)
    assert any("连续递增" in issue for issue in issues)
    assert any("循环" in issue for issue in issues)
    assert any("必须先于" in issue for issue in issues)
    shots.append(deepcopy(second))
    assert any(
        "编号重复" in issue for issue in validate_storyboard(analysis, outline, shots, paragraphs)
    )


def test_missing_evidence_and_self_dependency() -> None:
    analysis, outline, shots, paragraphs = storyboard()
    analysis.events[0].source_paragraph_ids = []
    shots[0].depends_on_shot_ids = ["SH001", "unknown"]
    issues = validate_storyboard(analysis, outline, shots, paragraphs)
    assert any("缺少原文依据" in issue for issue in issues)
    assert any("依赖引用不存在" in issue for issue in issues)
    assert any("循环" in issue for issue in issues)
