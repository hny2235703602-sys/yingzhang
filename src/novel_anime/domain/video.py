"""把故事脚本编译成 ComfyUI 原生 Wan2.2 TI2V-5B 工作流可用的视频提示词。

时长不固定：以 15 秒为目标把相邻分镜合并成一组，合并后超过目标但仍在上限内就整组保留，
例如 13 秒与 6 秒合成一组 19 秒；任何情况下都不把一镜拆到两个视频里。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

VIDEO_TARGET_SECONDS = 15.0
VIDEO_MAX_SECONDS = 20.0
# Wan2.2 TI2V-5B 官方工作流按 24 FPS 输出。
VIDEO_FPS = 24
# 官方示例的 1280x704 共 121 帧；超过这个长度画质与显存都不划算，所以按段生成。
VIDEO_PASS_SECONDS = 5.0
VIDEO_MAX_FRAMES = 121
VIDEO_MIN_FRAMES = 5
# 画幅沿用官方工作流的 1280x704（16:9），竖屏与其转置；1:1 取 32 的整数倍。
VIDEO_SIZES = {"16:9": (1280, 704), "9:16": (704, 1280), "1:1": (960, 960)}
VIDEO_NEGATIVE_PROMPT = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，"
    "最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，"
    "画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，"
    "三条腿，背景人很多，倒着走"
)


def frame_count(seconds: float, fps: int = VIDEO_FPS) -> int:
    """Wan 的 length 必须满足 4n+1，取最接近目标时长的帧数。"""
    frames = max(VIDEO_MIN_FRAMES, round(max(seconds, 0.0) * fps))
    steps = max(0, round((frames - 1) / 4))
    return min(4 * steps + 1, VIDEO_MAX_FRAMES)


def frames_to_seconds(frames: int, fps: int = VIDEO_FPS) -> float:
    return round(frames / fps, 2)


def total_seconds(shots: Sequence[dict[str, Any]]) -> float:
    return round(sum(float(shot.get("duration_seconds") or 0) for shot in shots), 3)


def group_shots(
    shots: Sequence[dict[str, Any]],
    target: float = VIDEO_TARGET_SECONDS,
    max_seconds: float = VIDEO_MAX_SECONDS,
) -> list[list[dict[str, Any]]]:
    """按顺序合并分镜：够目标时长就收组，放不下整镜就先收组。

    单镜超过上限时自成一组，绝不切开，避免一个动作横跨两个视频文件。
    """
    ordered = sorted(shots, key=lambda shot: int(shot.get("order") or 0))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_seconds = 0.0
    for shot in ordered:
        duration = float(shot.get("duration_seconds") or 0)
        if current and (current_seconds >= target or current_seconds + duration > max_seconds):
            groups.append(current)
            current, current_seconds = [], 0.0
        current.append(shot)
        current_seconds += duration
    if current:
        groups.append(current)
    # 末组太短就并回上一组，只要合并后仍在上限内，这样 15 秒加 5 秒会成为一个 20 秒的视频。
    while len(groups) > 1:
        merged = total_seconds(groups[-2]) + total_seconds(groups[-1])
        if merged > max_seconds + 1e-9:
            break
        groups[-2:] = [groups[-2] + groups[-1]]
    return groups


def split_passes(
    shots: Sequence[dict[str, Any]], pass_seconds: float = VIDEO_PASS_SECONDS
) -> list[dict[str, Any]]:
    """一组视频按每段不超过 5 秒切分成多次生成，后一段以前一段末帧续写。

    官方 5B 工作流单次只给到 121 帧（约 5 秒），更长的组必须分段拼，不能悄悄截断。
    """
    duration = total_seconds(shots)
    passes: list[dict[str, Any]] = []
    start = 0.0
    while start < duration - 1e-9:
        length = min(pass_seconds, duration - start)
        cursor = 0.0
        inside: list[dict[str, Any]] = []
        for shot in shots:
            shot_seconds = float(shot.get("duration_seconds") or 0)
            begin, end = cursor, cursor + shot_seconds
            overlap = min(end, start + length) - max(begin, start)
            # 只带上一段里真正出现的镜头，擦边 0.4 秒以内不算，免得提示词堆满已经演完的镜头。
            if overlap > 0.4 or (overlap > 0 and not inside):
                inside.append(shot)
            cursor = end
        frames = frame_count(length)
        passes.append(
            {
                "index": len(passes) + 1,
                "frames": frames,
                "seconds": frames_to_seconds(frames),
                "shot_ids": [str(shot.get("shot_id", "")) for shot in inside],
                "shots": inside,
                "continuation": bool(passes),
            }
        )
        start += length
    return passes


def _clean(parts: Sequence[str]) -> str:
    return "，".join(part.strip(" ，。；\n\t") for part in parts if part and part.strip())


def shot_prompt(shot: dict[str, Any], names: dict[str, str]) -> str:
    """单镜提示词：先场景与人物，再画面与动作，最后景别与运镜。"""
    people = [names.get(str(key), str(key)) for key in shot.get("character_ids", [])]
    lead = _clean(
        [
            str(shot.get("setting") or ""),
            f"画面中是{'、'.join(dict.fromkeys(people))}" if people else "",
            str(shot.get("frame") or ""),
        ]
    )
    body = _clean([str(shot.get("action") or "")])
    camera = _clean([str(shot.get("shot_size") or ""), str(shot.get("camera") or "")])
    text = lead or "延续上一镜的画面"
    if body:
        text += f"。{body}"
    dialogue = str(shot.get("dialogue") or "").strip()
    if dialogue:
        text += f"。台词「{dialogue}」，口型与神态随台词变化，画面不出现字幕"
    if camera:
        text += f"。镜头：{camera}，运动平稳"
    return text + "。"


def build_prompt(shots: Sequence[dict[str, Any]], names: dict[str, str], style: str = "") -> str:
    """整组提示词：按顺序描述每一镜，用「紧接着」交代时间推进。"""
    lines = [shot_prompt(shot, names) for shot in shots]
    text = "紧接着，".join(lines) if len(lines) > 1 else (lines[0] if lines else "")
    style_text = style.strip()
    if style_text:
        text = f"{text}\n风格与限制：{style_text}"
    return text


def workflow_preset(aspect_ratio: str = "16:9") -> dict[str, Any]:
    """ComfyUI 官方 Wan2.2 TI2V-5B 图生视频工作流的可复制参数。"""
    width, height = VIDEO_SIZES.get(aspect_ratio, VIDEO_SIZES["16:9"])
    return {
        "key": "wan22_ti2v_5b",
        "name": "Wan2.2 TI2V-5B 图生视频（ComfyUI 原生工作流）",
        "source": "https://comfyanonymous.github.io/ComfyUI_examples/wan22/",
        "docs": "https://docs.comfy.org/tutorials/video/wan/wan2-2",
        "width": width,
        "height": height,
        "fps": VIDEO_FPS,
        "frames": frame_count(VIDEO_PASS_SECONDS),
        "env": {
            "unet": "wan2.2_ti2v_5B_fp16.safetensors",
            "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "vae": "wan2.2_vae.safetensors",
        },
        "sampler": {
            "steps": 30,
            "cfg": 5.0,
            "sampler_name": "uni_pc",
            "scheduler": "simple",
            "denoise": 1.0,
            "shift": 8.0,
            "seed": "randomize",
        },
        "output": {"format": "WEBM", "codec": "vp9", "fps": VIDEO_FPS},
        "nodes": [
            "Wan22ImageToVideoLatent：width / height / length（4n+1）/ batch_size=1",
            "KSampler：steps 30，cfg 5，uni_pc + simple，denoise 1",
            "ModelSamplingSD3：shift 8",
            "SaveWEBM：vp9，24 fps",
        ],
        "negative": VIDEO_NEGATIVE_PROMPT,
    }


def compile_plan(
    shots: Sequence[dict[str, Any]],
    characters: Sequence[dict[str, Any]],
    style: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """把本章分镜编译成视频组、每段帧数与提示词。"""
    target = float(config.get("target_seconds") or VIDEO_TARGET_SECONDS)
    max_seconds = float(config.get("max_seconds") or VIDEO_MAX_SECONDS)
    names = {
        str(person.get("character_id")): str(person.get("name") or person.get("character_id"))
        for person in characters
    }
    preset = workflow_preset(str(config.get("aspect_ratio") or "16:9"))
    groups: list[dict[str, Any]] = []
    for index, group in enumerate(group_shots(shots, target, max_seconds), 1):
        passes = split_passes(group)
        for item in passes:
            body = build_prompt(item.pop("shots"), names, style)
            # 第二段起以视频末帧为输入，提示词先说明续写，避免模型重新起势。
            item["prompt"] = (
                f"接上一段最后一帧继续，不换场。{body}" if item["continuation"] else body
            )
        groups.append(
            {
                "group_id": f"G{index:03d}",
                "order": index,
                "shot_ids": [str(shot.get("shot_id", "")) for shot in group],
                "seconds": total_seconds(group),
                "frames": sum(int(item["frames"]) for item in passes),
                "prompt": build_prompt(group, names, style),
                "passes": passes,
            }
        )
    return {"groups": groups, "preset": preset, "negative": VIDEO_NEGATIVE_PROMPT}
