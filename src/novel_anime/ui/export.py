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


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\r\n", "\n").replace("\n", "<br>").replace("|", "\\|").strip()


def export_markdown(payload: dict[str, Any], secrets: tuple[str, ...] = ()) -> str:
    """Render the story script as a portable table mirroring the on-screen columns."""
    clean = safe_payload(payload, secrets)
    result = clean.get("result") or {}
    script = result.get("script") or {}
    rows = script.get("shots") or result.get("shots") or []
    lines = [
        "# 映章 · 故事脚本",
        "",
        f"运行 ID：{clean.get('id', '')}",
        f"版本：{clean.get('revision', '')}",
        f"状态：{clean.get('status', '')}",
        "",
        "## 故事脚本",
        "",
        "| 编号 | 时长 | 分镜 | 动作 | 台词 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, 1):
        shot_id = row.get("shot_id") or f"SH{index:03d}"
        storyboard = " · ".join(
            part
            for part in (
                row.get("shot_size", ""),
                row.get("camera", ""),
                row.get("frame", ""),
            )
            if part
        )
        duration = row.get("duration_seconds")
        lines.append(
            "| {id} | {duration} | {storyboard} | {action} | {dialogue} |".format(
                id=_cell(shot_id),
                duration=_cell(f"{duration:g} 秒" if isinstance(duration, (int, float)) else ""),
                storyboard=_cell(storyboard),
                action=_cell(row.get("action")),
                dialogue=_cell(row.get("dialogue")) or "—",
            )
        )
    total = sum(float(row.get("duration_seconds") or 0) for row in rows if isinstance(row, dict))
    lines.extend(["", f"共 {len(rows)} 镜 · 规划时长约 {total:g} 秒。", ""])
    characters = script.get("characters") or []
    if characters:
        lines.extend(["## 本章人物", ""])
        lines.append("| 编号 | 姓名 | 外观 | 服装 |")
        lines.append("| --- | --- | --- | --- |")
        for person in characters:
            lines.append(
                "| {id} | {name} | {appearance} | {costume} |".format(
                    id=_cell(person.get("character_id")),
                    name=_cell(person.get("name")),
                    appearance=_cell(person.get("appearance")),
                    costume=_cell(person.get("costume")),
                )
            )
        lines.append("")
    for heading, value in [
        ("审阅与待核实", result.get("validation_issues") or []),
        ("来源段落", result.get("paragraphs", [])),
        ("执行来源", result.get("provenance", [])),
        ("采用时的输入与配置快照", clean.get("document", {})),
    ]:
        lines.extend([f"## {heading}", "", "```json", export_json(value), "```", ""])
    return "\n".join(lines)
