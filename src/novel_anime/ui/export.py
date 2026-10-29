"""Pure, credential-free presentation exports for the studio UI."""

from __future__ import annotations

import json
import re
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

_SECRET_FIELD = re.compile(
    r"(^|_)(api_key|token|secret|password|authorization|credential)($|_)", re.I
)


def export_prompt_excel(entry: dict[str, Any]) -> bytes:
    """Export a saved chapter snapshot as a native XLSX, with literal text cells."""
    content = entry["content"]
    rows = [
        [content.get("chapter_title", ""), f"收藏版本 v{content.get('revision', '')}"],
        ["收藏时间", entry.get("created_at", "")],
        ["片段组号", "组时长（秒）", "每组提示词", "分镜编号", "分镜时长（秒）",
         "每个分镜提示词", "关键帧提示词"],
    ]
    for group in content.get("groups", []):
        for shot in group.get("shot_prompts", []):
            rows.append([
                group.get("group_id", ""), group.get("seconds", ""),
                group.get("prompt", ""), shot.get("shot_id", ""),
                shot.get("duration_seconds", ""), shot.get("prompt", ""),
                shot.get("keyframe_prompt", ""),
            ])

    sheet_rows = []
    for index, values in enumerate(rows, 1):
        cells = []
        for column, value in enumerate(values):
            text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))
            # Excel limits individual cells to 32767 characters. Never silently truncate.
            if len(text.encode("utf-16-le")) // 2 > 32767:
                from novel_anime.core.errors import AppError

                raise AppError("invalid_input", "提示词超过 Excel 单元格字数上限，请缩短后导出。")
            style = 1 if index == 3 else 0
            cells.append(
                f'<c r="{chr(65 + column)}{index}" s="{style}" t="inlineStr">'
                f'<is><t xml:space="preserve">{escape(text)}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{index}">' + "".join(cells) + '</row>')
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    files = {
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.styles+xml"/></Types>'
        ),
        "_rels/.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            f'<workbook xmlns="{namespace}" xmlns:r="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships"><sheets><sheet name="本章分镜" sheetId="1" '
            'r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/styles" Target="styles.xml"/></Relationships>'
        ),
        "xl/styles.xml": (
            f'<styleSheet xmlns="{namespace}"><fonts count="2"><font><sz val="11"/>'
            '<name val="等线"/></font><font><b/><sz val="11"/><name val="等线"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
            '<cellXfs count="2"><xf fontId="0" applyAlignment="1"><alignment vertical="top" '
            'wrapText="1"/></xf><xf fontId="1" applyAlignment="1"><alignment vertical="top" '
            'wrapText="1"/></xf></cellXfs></styleSheet>'
        ),
        "xl/worksheets/sheet1.xml": (
            f'<worksheet xmlns="{namespace}"><sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="3" topLeftCell="A4" activePane="bottomLeft" state="frozen"/>'
            '</sheetView></sheetViews><cols>'
            + ''.join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'
                      for i, width in enumerate([16, 16, 65, 16, 18, 65, 65], 1))
            + '</cols><sheetData>' + ''.join(sheet_rows)
            + f'</sheetData><autoFilter ref="A3:G{len(rows)}"/></worksheet>'
        ),
    }
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, xml in files.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8"?>' + xml)
    return output.getvalue()


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
