"""Pure, credential-free presentation exports for the studio UI."""

from __future__ import annotations

import json
import re
from typing import Any

_SECRET_FIELD = re.compile(
    r"(^|_)(api_key|token|secret|password|authorization|credential)($|_)", re.I
)


def safe_payload(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Remove credential fields and redact known environment secret values recursively."""
    if isinstance(value, dict):
        return {
            str(key): safe_payload(item, secrets)
            for key, item in value.items()
            if str(key) == "api_key_env" or not _SECRET_FIELD.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [safe_payload(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[已脱敏]")
        return re.sub(r"(?i)bearer\s+[^\s\"']+", "Bearer [已脱敏]", value)
    return value


def export_json(payload: dict[str, Any], secrets: tuple[str, ...] = ()) -> str:
    """Serialize the complete versioned snapshot without credentials."""
    return json.dumps(safe_payload(payload, secrets), ensure_ascii=False, indent=2, default=str)


def export_markdown(payload: dict[str, Any], secrets: tuple[str, ...] = ()) -> str:
    """Render a portable storyboard with original references and provenance."""
    clean = safe_payload(payload, secrets)
    result = clean.get("result") or {}
    lines = [
        "# 映章 · 章节分镜",
        "",
        f"运行 ID：{clean.get('id', '')}",
        f"版本：{clean.get('revision', '')}",
        f"状态：{clean.get('status', '')}",
        "",
    ]
    for heading, key in [("本章分析", "analysis"), ("改编大纲", "outline")]:
        lines.extend([f"## {heading}", "", "```json", export_json(result.get(key, {})), "```", ""])
    for index, shot in enumerate(result.get("shots", []), 1):
        lines.extend(
            [
                f"## 镜头 {index:03d} · {shot.get('title', shot.get('shot_id', ''))}",
                "",
                "```json",
                export_json(shot),
                "```",
                "",
            ]
        )
    for heading, value in [
        ("来源段落", result.get("paragraphs", [])),
        ("审阅与待核实", result.get("review", {})),
        ("执行来源", result.get("provenance", [])),
        ("采用时的输入与配置快照", clean.get("document", {})),
    ]:
        lines.extend([f"## {heading}", "", "```json", export_json(value), "```", ""])
    return "\n".join(lines)
