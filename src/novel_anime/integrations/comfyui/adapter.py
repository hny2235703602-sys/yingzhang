"""Reserved integration boundary: video remains disabled until a tested workflow is supplied."""

from typing import Any

from novel_anime.core.errors import AppError


def submit_video(shot: dict[str, Any], workflow: dict[str, Any] | None = None) -> None:
    raise AppError(
        "comfyui_not_configured",
        "尚未接入已验证的 ComfyUI API 工作流。当前仅生成文本分镜与提示词。",
    )
