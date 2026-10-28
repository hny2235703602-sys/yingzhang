"""视频提示词分组：时长不固定，但要可预测、可复核。"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from novel_anime.application.service import StudioService
from novel_anime.core.errors import AppError
from novel_anime.core.settings import ROOT
from novel_anime.domain.models import ModelProfile
from novel_anime.domain.video import (
    VIDEO_NEGATIVE_PROMPT,
    build_prompt,
    compile_plan,
    frame_count,
    group_shots,
    split_passes,
    workflow_preset,
)
from novel_anime.persistence.database import RunRow
from novel_anime.ui.web_server import dispatch


def _shot(order: int, seconds: float, **extra: object) -> dict[str, object]:
    return {
        "shot_id": f"s{order}",
        "order": order,
        "duration_seconds": seconds,
        "shot_size": "中景",
        "camera": "固定",
        "frame": f"画面 {order}",
        "action": f"动作 {order}",
        "dialogue": "",
        "character_ids": [],
        "setting": "庭院",
        **extra,
    }


def test_groups_join_shots_until_target_and_never_split_one_shot():
    # 13 秒已经接近目标，再来 6 秒就整镜并入，成为一组 19 秒。
    groups = group_shots([_shot(1, 13), _shot(2, 6)])
    assert [[shot["shot_id"] for shot in group] for group in groups] == [["s1", "s2"]]


def test_group_stops_when_next_shot_would_pass_the_ceiling():
    groups = group_shots([_shot(1, 10), _shot(2, 8), _shot(3, 5)])
    assert [[shot["shot_id"] for shot in group] for group in groups] == [["s1", "s2"], ["s3"]]


def test_oversized_single_shot_stays_alone_instead_of_being_split():
    groups = group_shots([_shot(1, 24), _shot(2, 4)])
    assert [[shot["shot_id"] for shot in group] for group in groups] == [["s1"], ["s2"]]


def test_short_tail_group_merges_back_when_it_still_fits():
    groups = group_shots([_shot(1, 11), _shot(2, 3), _shot(3, 2)])
    assert [[shot["shot_id"] for shot in group] for group in groups] == [["s1", "s2", "s3"]]


def test_frame_count_snaps_to_four_n_plus_one_and_caps_at_one_pass():
    assert [frame_count(seconds) for seconds in (0.2, 1, 2, 5, 19)] == [5, 25, 49, 121, 121]
    assert all(frame_count(seconds) % 4 == 1 for seconds in (0.2, 1, 2, 5, 19))


def test_long_group_is_split_into_continuation_passes_instead_of_truncated():
    passes = split_passes([_shot(1, 13), _shot(2, 6)])
    assert [item["shot_ids"] for item in passes] == [["s1"], ["s1"], ["s1", "s2"], ["s2"]]
    assert [item["frames"] for item in passes] == [121, 121, 121, 97]
    assert [item["continuation"] for item in passes] == [False, True, True, True]


def test_prompt_keeps_scene_cast_action_camera_and_dialogue_in_one_line():
    names = {"hero": "少年"}
    shot = _shot(1, 5, character_ids=["hero"], dialogue="少年：“到了。”", shot_size="近景")
    text = build_prompt([shot], names, "二维动画")
    for fragment in ("庭院", "少年", "画面 1", "动作 1", "近景", "台词", "二维动画"):
        assert fragment in text
    assert "字幕" in text


def test_plan_compiles_groups_passes_and_workflow_parameters():
    plan = compile_plan(
        [_shot(1, 13), _shot(2, 6)],
        [{"character_id": "hero", "name": "少年"}],
        "二维动画",
        {},
    )
    assert [group["group_id"] for group in plan["groups"]] == ["G001"]
    group = plan["groups"][0]
    assert group["seconds"] == 19
    assert group["frames"] == 460
    assert [item["index"] for item in group["passes"]] == [1, 2, 3, 4]
    assert group["passes"][1]["prompt"].startswith("接上一段最后一帧继续")
    assert plan["negative"] == VIDEO_NEGATIVE_PROMPT
    assert plan["preset"]["width"] == 1280 and plan["preset"]["height"] == 704
    assert plan["preset"]["fps"] == 24


def test_portrait_preset_swaps_dimensions_and_keeps_official_sampler():
    preset = workflow_preset("9:16")
    assert (preset["width"], preset["height"]) == (704, 1280)
    assert preset["sampler"]["sampler_name"] == "uni_pc"
    assert preset["sampler"]["steps"] == 30 and preset["sampler"]["shift"] == 8
    assert preset["env"]["unet"] == "wan2.2_ti2v_5B_fp16.safetensors"


@pytest.fixture
def service(monkeypatch, tmp_path: Path) -> StudioService:
    monkeypatch.setenv("YINGZHANG_DATA_DIR", str(tmp_path))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")
    return StudioService()


def _run_with_script(service: StudioService, seconds: list[float]) -> str:
    work = service.create_work("视频作品")
    chapter = service.save_chapter(work, "第一章", "少年走进庭院。", "")
    profile = ModelProfile(
        provider="test",
        protocol="chat_completions",
        base_url="https://example.com/v1",
        model="test",
    ).model_dump()
    run_id = service.create_run(chapter, profile, "二维动画", "16:9", "", "video-plan")
    shots = [
        {
            **_shot(index, value, character_ids=["hero"]),
            "source_paragraph_ids": ["P0001"],
            "generation_status": "not_submitted",
        }
        for index, value in enumerate(seconds, 1)
    ]
    with service.sessions.begin() as session:
        row = session.get(RunRow, run_id)
        row.result = {
            "script": {
                "characters": [
                    {
                        "character_id": "hero",
                        "name": "少年",
                        "appearance": "黑发少年",
                        "costume": "蓝色长袍",
                    }
                ],
                "shots": shots,
            },
            "shots": shots,
            "paragraphs": [{"paragraph_id": "P0001", "text": "少年走进庭院。"}],
        }
        row.status = "succeeded"
    return chapter


def test_video_plan_reads_script_without_creating_a_run(service: StudioService):
    chapter = _run_with_script(service, [13, 6, 5])
    before = len(service.list_runs(chapter))
    plan = service.video_plan(chapter)
    assert len(service.list_runs(chapter)) == before
    assert [group["seconds"] for group in plan["groups"]] == [19, 5]
    assert plan["run"]["id"]
    assert plan["target_seconds"] == 15 and plan["max_seconds"] == 20


def test_video_plan_is_empty_before_any_script(service: StudioService):
    work = service.create_work("空作品")
    chapter = service.save_chapter(work, "第一章", "少年走进庭院。", "")
    plan = service.video_plan(chapter)
    assert plan["groups"] == []
    assert plan["run"] is None


def test_saved_video_template_replaces_the_style_line(service: StudioService):
    chapter = _run_with_script(service, [5])
    assert "平涂上色" in service.video_plan(chapter)["groups"][0]["prompt"]
    dispatch(service, "prompts", {"category": "video", "template": "水墨动画，留白"})
    prompt = service.video_plan(chapter)["groups"][0]["prompt"]
    assert "水墨动画，留白" in prompt
    assert "平涂上色" not in prompt


def test_saved_group_settings_change_the_plan(service: StudioService):
    chapter = _run_with_script(service, [8, 8])
    dispatch(
        service, "video-config", {"target_seconds": 8, "max_seconds": 16, "aspect_ratio": "9:16"}
    )
    plan = service.video_plan(chapter)
    assert [group["seconds"] for group in plan["groups"]] == [16]
    assert plan["aspect_ratio"] == "9:16"
    assert plan["preset"]["width"] == 704


def test_video_config_keeps_keys_it_does_not_edit(service: StudioService):
    service.save_video_config({"base_url": "http://127.0.0.1:8188", "workflow": "custom"})
    dispatch(service, "video-config", {"target_seconds": 12, "max_seconds": 18})
    config = service.load_video_config()
    assert config["base_url"] == "http://127.0.0.1:8188"
    assert config["workflow"] == "custom"
    assert config["target_seconds"] == 12 and config["max_seconds"] == 18
    assert config["aspect_ratio"] == "16:9"
    dispatch(service, "video-config", {"base_url": "http://127.0.0.1:8288"})
    assert service.load_video_config()["base_url"] == "http://127.0.0.1:8288"


def test_video_config_rejects_bad_duration_or_ratio(service: StudioService):
    for body in (
        {"target_seconds": 30, "max_seconds": 20},
        {"target_seconds": 15, "max_seconds": 61},
        {"target_seconds": 3, "max_seconds": 20},
        {"aspect_ratio": "4:3"},
        {"target_seconds": True, "max_seconds": 20},
        {"target_seconds": "15"},
    ):
        with pytest.raises(AppError):
            dispatch(service, "video-config", body)
    assert service.load_video_config() == {}


def test_group_prompt_has_exact_timeline_and_separate_constraints():
    prompt = build_prompt([_shot(1, 5), _shot(2, 5), _shot(3, 5)], {}, "照片质感，柔和环境光")
    assert "0-5秒：" in prompt and "5-10秒：" in prompt and "10-15秒：" in prompt
    assert prompt.index("照片质感") < prompt.index("0-5秒") < prompt.index("镜头要求：")
    assert "角色不说话无台词" in prompt and "无背景音乐" in prompt
    assert "额外人偶" in prompt


def test_group_prompt_keeps_dialogue_instead_of_silencing_speakers():
    prompt = build_prompt([_shot(1, 3.5, dialogue="少年：“到了。”")], {})
    assert "0-3.5秒：" in prompt and "少年：“到了。”" in prompt
    assert "角色不说话无台词" not in prompt
    assert "不增加台词" in prompt


def test_english_generation_is_explicit_cached_and_invalidates_on_source_change(
    service: StudioService, monkeypatch
):
    from novel_anime.domain.video import EnglishVideoPrompt
    from novel_anime.integrations.language_models.client import LLMClient

    chapter = _run_with_script(service, [5])
    service.save_profile(
        ModelProfile(
            provider="test",
            protocol="chat_completions",
            base_url="https://example.com/v1",
            model="test",
        ).model_dump()
    )
    calls = []

    def generate(self, schema, system, payload, stage):
        calls.append(payload)
        assert "0-5秒：" in payload["chinese_prompt"]
        return EnglishVideoPrompt(
            integrated_multimodal_description="[Shot 1] 0-5s: A boy walks in a courtyard.",
            overall_soundscape="Footsteps. No speech or dialogue.",
            non_diegetic_music="None (no background music).",
        ), {}

    monkeypatch.setattr(LLMClient, "generate", generate)
    assert service.video_plan(chapter)["groups"][0]["prompt_en"] == ""
    assert not calls
    response = dispatch(
        service,
        "video-prompt-english",
        {
            "chapter_id": chapter,
            "group_id": "G001",
            "profile_id": "online",
        },
    )
    assert response["prompt_en"].startswith("integrated_multimodal_description: [Shot 1]")
    assert "\n\noverall_soundscape: " in response["prompt_en"]
    assert "\n\nnon_diegetic_music: " in response["prompt_en"]
    assert service.video_plan(chapter)["groups"][0]["prompt_en"] == response["prompt_en"]
    assert len(calls) == 1
    dispatch(service, "prompts", {"category": "video", "template": "水墨风格"})
    assert service.video_plan(chapter)["groups"][0]["prompt_en"] == ""
    assert len(calls) == 1
