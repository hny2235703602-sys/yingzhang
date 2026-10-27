from copy import deepcopy

import pytest
from pydantic import ValidationError

from novel_anime.domain.models import (
    InputDocument,
    ModelProfile,
    ScriptCharacter,
    ScriptRow,
    ScriptShot,
    SourceParagraph,
    StoryScript,
)
from novel_anime.domain.validation import MAX_SHOT_SECONDS, validate_script


def script() -> tuple[StoryScript, list[SourceParagraph]]:
    paragraphs = [SourceParagraph(paragraph_id="P0001", text="少年推开殿门。")]
    return (
        StoryScript(
            characters=[
                ScriptCharacter(
                    character_id="hero", name="少年", appearance="黑发，眉骨高", costume="蓝袍"
                )
            ],
            shots=[
                ScriptShot(
                    shot_id="SH001",
                    order=1,
                    duration_seconds=5,
                    shot_size="中景",
                    camera="缓慢推进",
                    frame="少年双手推在殿门板上，门缝里透出微光",
                    action="少年推开殿门",
                    dialogue="少年：“有人吗？”",
                    character_ids=["hero"],
                    setting="大殿门外，雨夜",
                    source_paragraph_ids=["P0001"],
                )
            ],
        ),
        paragraphs,
    )


def test_valid_script_has_no_issues() -> None:
    assert validate_script(*script()) == []


def test_contract_forbids_extra_and_empty_required_fields() -> None:
    with pytest.raises(ValidationError):
        InputDocument(work_id="w", title="t", text=" ")
    with pytest.raises(ValidationError):
        ScriptRow(shot_size="中景", camera="固定", frame="画面", action="推门", invented=True)
    with pytest.raises(ValidationError):
        ScriptRow(shot_size="大远景", camera="固定", frame="画面", action="推门")
    with pytest.raises(ValidationError):
        ScriptRow(shot_size="中景", camera="  ", frame="画面", action="推门")
    with pytest.raises(ValidationError):
        SourceParagraph(paragraph_id="../../bad", text="x")
    with pytest.raises(ValidationError):
        ScriptShot(
            shot_id="SH001",
            order=0,
            shot_size="中景",
            camera="固定",
            frame="画面",
            action="推门",
        )


def test_mutable_defaults_are_independent() -> None:
    first = StoryScript()
    second = StoryScript()
    first.characters.append(ScriptCharacter(character_id="a", name="甲", appearance="黑衣"))
    assert second.characters == []
    assert (
        ScriptShot(
            shot_id="SH001", order=1, shot_size="全景", camera="固定", frame="画面", action="动作"
        ).character_ids
        == []
    )


def test_duration_bounds() -> None:
    with pytest.raises(ValidationError):
        ScriptRow(shot_size="中景", camera="固定", frame="画面", action="推门", duration_seconds=0)
    with pytest.raises(ValidationError):
        ScriptRow(
            shot_size="中景", camera="固定", frame="画面", action="推门", duration_seconds=121
        )
    assert MAX_SHOT_SECONDS < 121


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
    story, paragraphs = script()
    shot = story.shots[0]
    shot.character_ids = ["missing"]
    shot.source_paragraph_ids = ["missing"]
    issues = validate_script(story, paragraphs)
    assert any("角色引用不存在" in issue for issue in issues)
    assert any("原文引用不存在" in issue for issue in issues)
    assert any("原文段落未被写入脚本" in issue for issue in issues)


def test_order_duration_and_duplicate_ids() -> None:
    story, paragraphs = script()
    story.shots[0].duration_seconds = 20
    issues = validate_script(story, paragraphs)
    assert any("超过 15 秒建议拆分" in issue for issue in issues)
    second = deepcopy(story.shots[0])
    second.order = 3
    second.duration_seconds = 5
    second.character_ids = ["hero", "hero"]
    story.shots.append(second)
    issues = validate_script(story, paragraphs)
    assert any("镜头编号重复" in issue for issue in issues)
    assert any("连续递增" in issue for issue in issues)
    assert any("含重复角色引用" in issue for issue in issues)


def test_duplicate_character_and_missing_appearance() -> None:
    story, paragraphs = script()
    story.characters.append(
        ScriptCharacter(character_id="hero", name="少年", appearance="另一套外观")
    )
    issues = validate_script(story, paragraphs)
    assert any("角色编号重复" in issue for issue in issues)
    story.characters = [
        ScriptCharacter(character_id="hero", name="少年", appearance="x"),
    ]
    assert validate_script(story, paragraphs) == []


def test_missing_evidence_and_empty_script() -> None:
    story, paragraphs = script()
    story.shots[0].source_paragraph_ids = []
    assert any("缺少原文依据" in issue for issue in validate_script(story, paragraphs))
    assert any("脚本没有任何分镜" in issue for issue in validate_script(StoryScript(), paragraphs))
