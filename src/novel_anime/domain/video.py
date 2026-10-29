"""把故事脚本编译成 ComfyUI 原生 Wan2.2 TI2V-5B 工作流可用的视频提示词。

时长不固定：以 15 秒为目标把相邻分镜合并成一组，合并后超过目标但仍在上限内就整组保留，
例如 13 秒与 6 秒合成一组 19 秒；任何情况下都不把一镜拆到两个视频里。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

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
    "动漫，插画，线稿，平涂，卡通脸，塑料皮肤，过度磨皮，色调艳丽，过曝，静态，细节模糊不清，字幕，"
    "风格，作品，画作，画面，静止，整体发灰，"
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
    """单镜提示词：先真实场景与人物，再微表情/动作，最后景别与运镜。"""
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
    return text + "。真人影视摄影，保留自然皮肤纹理与细微表演，环境光线和空间透视连续。"


class EnglishVideoPrompt(BaseModel):
    integrated_multimodal_description: str = Field(min_length=1)
    overall_soundscape: str = Field(min_length=1)
    non_diegetic_music: str = Field(min_length=1)

    def render(self) -> str:
        return "\n\n".join(f"{key}: {value}" for key, value in self.model_dump().items())


VIDEO_TRANSLATION_INSTRUCTION = """将给定中文视频整组提示词忠实转换成英文视频提示词。
integrated_multimodal_description 按 [Shot 1]、[Shot 2] 顺序，明确每镜起止秒数、
场景质感、角色外观/比例/道具、画面动作、景别与运镜和所有物品限制。
保留 <Picture N> 等参考图标记，不添加输入中没有的角色、物品、对白、运镜或配乐。
真实对白才使用声音身份 (S1) 等以及 <d>[语言] 原台词</d>，原台词保留原语言与原文，
旁白、心声和画外音保持其声音类型，不驱动画面中角色口型。
禁止把中文对白改成角色说英语；无台词就明确 no speech or dialogue，绝不编造配音。
overall_soundscape 只描述画面内动作/环境音及输入要求；
non_diegetic_music 输入没有明确指定配乐时必须为 None (no background music)。
三项内容都用英文写（人名、参考图标记与原台词除外）。保留所有否定限制与时间顺序。
"""


def build_prompt(shots: Sequence[dict[str, Any]], names: dict[str, str], style: str = "") -> str:
    """场景角色、时间轴、镜头限制与声音分别成段，内容仅取自脚本。"""
    settings = list(
        dict.fromkeys(str(s.get("setting") or "").strip().rstrip("。；，") for s in shots)
    )
    people = list(
        dict.fromkeys(
            names.get(str(key), str(key)) for shot in shots for key in shot.get("character_ids", [])
        )
    )
    intro = "。".join(
        value for value in [style.strip().rstrip("。"), "、".join(filter(None, settings))] if value
    )
    lines = [intro + "。"] if intro else []
    if people:
        lines.append(
            f"角色是{'、'.join(people)}，人物外观、服装、体型比例与参考图保持一致，道具在动作中连续一致。"
        )
    timeline = []
    cameras = []
    cursor = 0.0
    for shot in shots:
        end = round(cursor + float(shot.get("duration_seconds") or 0), 3)
        action = _clean([str(shot.get("frame") or ""), str(shot.get("action") or "")])
        camera = _clean([str(shot.get("shot_size") or ""), str(shot.get("camera") or "")])
        if camera:
            cameras.append(camera)
        dialogue = str(shot.get("dialogue") or "").strip()
        details = [
            action,
            f"镜头：{camera}" if camera else "",
            f"台词：{dialogue}" if dialogue else "",
        ]
        body = "；".join(filter(None, details))
        ending = "" if body.endswith(("。", "！", "？", "”", "!", "?")) else "。"
        timeline.append(f"{cursor:g}-{end:g}秒：{body}{ending}")
        cursor = end
    lines.append("\n".join(timeline))
    camera_rules = "；".join(dict.fromkeys(cameras))
    lines.append(
        (f"镜头要求：{camera_rules}，严格遵守各时间段的景别与运镜。" if camera_rules else "")
        + "真人摄影质感；表演以可见微表情为核心，"
        + "保留眼神落点、眨眼、呼吸、嘴角、眉眼、下颌和手指的自然变化；"
        + "场景物品仅限分镜画面中已描述的物品与角色，不添加无关人物、玩偶、玩具或额外人偶；"
        + "环境保持真实空间透视、材质、光线方向、天气和接触阴影连续；不出现字幕、文字或水印。"
    )
    sound = "音效仅限与动作、碰撞及环境对应的声音"
    if any(str(shot.get("dialogue") or "").strip() for shot in shots):
        sound += (
            "，台词仅使用上方分镜原文；人物对白同步口型，"
            "旁白、心声与画外音不驱动画面中角色口型；不增加台词或旁白"
        )
    else:
        sound += "，角色不说话无台词"
    lines.append(sound + "，无背景音乐。")
    return "\n\n".join(filter(None, lines))


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
        str(person.get("character_id")): _clean(
            [
                str(person.get("name") or person.get("character_id")),
                str(person.get("appearance") or ""),
                str(person.get("costume") or ""),
            ]
        )
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
