from __future__ import annotations

import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from novel_anime.core.errors import AppError
from novel_anime.core.settings import ROOT
from novel_anime.domain.models import (
    ModelProfile,
    ScriptBatch,
    ScriptShot,
    SourceParagraph,
    StoryScript,
)
from novel_anime.domain.validation import validate_script
from novel_anime.integrations.language_models.client import LLMClient


class FlowState(TypedDict, total=False):
    result: dict[str, Any]


def paragraphs(text: str) -> list[SourceParagraph]:
    return [
        SourceParagraph(paragraph_id=f"P{i:04d}", text=value.strip())
        for i, value in enumerate(re.split(r"\n\s*\n|\n", text.strip()), 1)
        if value.strip()
    ]


class AdaptationFlow:
    """Save every finished batch before advancing; a resume never repeats saved rows."""

    def __init__(
        self, document: dict[str, Any], profile: ModelProfile, save: Any, boundary: Any
    ) -> None:
        self.document = document
        self.profile = profile
        self.client = LLMClient(profile)
        self.save = save
        self.boundary = boundary

    def _template(self, stage: str = "script") -> str:
        saved = self.document.get("prompt_templates", {}).get(stage)
        if saved:
            return str(saved)
        return (ROOT / "prompts" / f"{stage}.txt").read_text(encoding="utf-8")

    def _within_budget(self, schema: Any, payload: dict[str, Any], stage: str = "script") -> bool:
        # CJK costs more than JSON punctuation, so a flat two tokens per character
        # rejected a few thousand characters of short dialogue before any model call.
        if not self.profile.context_budget or not self.profile.max_output_tokens:
            return True
        return self.client.request_tokens(self._template(stage), payload, schema) <= (
            self.profile.context_budget - self.profile.max_output_tokens
        )

    def call(self, result: dict[str, Any], payload: dict[str, Any]) -> ScriptBatch:
        self.boundary("script")
        template = self._template()
        if not self._within_budget(ScriptBatch, payload):
            raise AppError("context_budget", "当前段落超过保守上下文预算，请提高预算或缩短章节。")
        model, meta = self.client.generate(ScriptBatch, template, payload, "script")
        result.setdefault("provenance", []).append(meta)
        return ScriptBatch.model_validate(model.model_dump())

    def run(self, initial: dict[str, Any], saver: Any, run_id: str) -> dict[str, Any]:
        graph = StateGraph(FlowState)
        graph.add_node("prepare", self.prepare)
        graph.add_node("script", self.script)
        graph.add_edge(START, "prepare")
        graph.add_edge("prepare", "script")
        graph.add_edge("script", END)
        app = graph.compile(checkpointer=saver)
        state = app.invoke(
            {"result": initial}, {"configurable": {"thread_id": run_id}, "recursion_limit": 100}
        )
        return dict(state["result"])

    def prepare(self, state: FlowState) -> FlowState:
        result = dict(state["result"])
        if "paragraphs" not in result:
            result.update(
                schema_version="2.0",
                prompt_template_version="2.0",
                paragraphs=[p.model_dump() for p in paragraphs(self.document["text"])],
                provenance=[],
                generation_status="not_submitted",
            )
            self.save(result, "prepare")
        return {"result": result}

    def _chunks(self, pending: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """Split pending paragraphs into consecutive groups that each fit the budget."""
        chunks: list[list[dict[str, Any]]] = []
        for paragraph in pending:
            if not self._within_budget(ScriptBatch, self._payload([paragraph], [], [])):
                raise AppError(
                    "paragraph_too_long", "某一段超过上下文预算，请增大预算或按语义换行。"
                )
            if chunks and self._within_budget(
                ScriptBatch, self._payload(chunks[-1] + [paragraph], [], [])
            ):
                chunks[-1].append(paragraph)
            else:
                chunks.append([paragraph])
        return chunks

    def _payload(
        self,
        chunk: list[dict[str, Any]],
        previous: list[dict[str, Any]],
        characters: list[dict[str, Any]],
    ) -> dict[str, Any]:
        known = [
            {
                "character_id": profile["character_id"],
                "name": profile["name"],
                "appearance": profile.get("appearance", ""),
                "costume": profile.get("costume", ""),
            }
            for profile in self.document.get("visual_profiles", [])
            if any(profile["name"] in paragraph["text"] for paragraph in chunk)
        ]
        seen = {item["character_id"] for item in known}
        known.extend(item for item in characters if item["character_id"] not in seen)
        return {
            "chapter": self.document["title"],
            "optional_context": self.document["context"],
            "style": self.document["style"],
            "aspect_ratio": self.document["aspect_ratio"],
            "preference": self.document["preference"],
            "paragraphs": chunk,
            "known_characters": known,
            "previous_rows": previous[-3:],
            "instruction": (
                "只覆盖本次 paragraphs，按原文顺序改编，不提前透露后文。"
                "source_paragraph_ids 只能引用本次给出的编号，且每行都要填写。"
                "character_ids 沿用 known_characters 中的 character_id；"
                "本次没有依据的新人物自行以 C_ 开头编号，并在 characters 中给出外观。"
                "各行时长与景别要有变化，避免连续同景别。"
            ),
        }

    def script(self, state: FlowState) -> FlowState:
        result = dict(state["result"])
        if "script" in result:
            return {"result": result}
        covered = set(result.get("script_covered") or [])
        covered_order = list(result.get("script_covered") or [])
        rows: list[dict[str, Any]] = list(result.get("script_rows") or [])
        characters: list[dict[str, Any]] = list(result.get("script_characters") or [])
        pending = [p for p in result["paragraphs"] if p["paragraph_id"] not in covered]
        for chunk in self._chunks(pending):
            batch = self.call(result, self._payload(chunk, rows, characters))
            if not batch.shots:
                raise AppError("empty_script", "模型没有输出分镜，已保存此前结果。")
            for person in batch.characters:
                known = next(
                    (item for item in characters if item["character_id"] == person.character_id),
                    None,
                )
                if known is None:
                    characters.append(person.model_dump())
                elif not known["appearance"].strip():
                    known["appearance"] = person.appearance
            for row in batch.shots:
                order = len(rows) + 1
                rows.append(
                    ScriptShot(
                        **row.model_dump(), shot_id=f"SH{order:03d}", order=order
                    ).model_dump()
                )
            covered.update(item["paragraph_id"] for item in chunk)
            covered_order.extend(item["paragraph_id"] for item in chunk)
            result["script_rows"] = rows
            result["script_characters"] = characters
            result["script_covered"] = covered_order
            result["shots"] = rows
            self.save(result, f"script {len(rows)}")
        if not rows:
            raise AppError("empty_script", "本章没有可改编的正文，请检查章节内容。")
        script = StoryScript.model_validate({"characters": characters, "shots": rows})
        result["script"] = script.model_dump()
        result["shots"] = [shot.model_dump() for shot in script.shots]
        result.pop("script_rows", None)
        result.pop("script_covered", None)
        result.pop("script_characters", None)
        result["validation_issues"] = validate_script(
            script, [SourceParagraph(**p) for p in result["paragraphs"]]
        )
        self.save(result, "script")
        return {"result": result}
