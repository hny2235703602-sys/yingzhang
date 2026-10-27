from __future__ import annotations

import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from novel_anime.core.errors import AppError
from novel_anime.core.settings import ROOT
from novel_anime.domain.models import (
    Analysis,
    ModelProfile,
    Outline,
    Review,
    Shot,
    ShotBatch,
    SourceParagraph,
)
from novel_anime.domain.validation import validate_storyboard
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
    """Persist each successful call before advancing; resume never repeats saved batches."""

    def __init__(
        self, document: dict[str, Any], profile: ModelProfile, save: Any, boundary: Any
    ) -> None:
        self.document = document
        self.profile = profile
        self.client = LLMClient(profile)
        self.save = save
        self.boundary = boundary

    def _template(self, stage: str) -> str:
        saved = self.document.get("prompt_templates", {}).get(stage)
        if saved:
            return str(saved)
        return (ROOT / "prompts" / f"{stage}.txt").read_text(encoding="utf-8")

    def _within_budget(self, schema: Any, stage: str, payload: dict[str, Any]) -> bool:
        # CJK costs more than JSON punctuation. A flat two tokens per character
        # rejected a few thousand characters of short dialogue before any model call.
        if not self.profile.context_budget or not self.profile.max_output_tokens:
            return True
        return self.client.request_tokens(self._template(stage), payload, schema) <= (
            self.profile.context_budget - self.profile.max_output_tokens
        )

    def call(self, result: dict[str, Any], schema: Any, stage: str, payload: dict[str, Any]) -> Any:
        self.boundary(stage)
        template = self._template(stage)
        if not self._within_budget(schema, stage, payload):
            raise AppError(
                "context_budget", "当前阶段内容超过保守上下文预算，请提高预算或缩短章节。"
            )
        model, meta = self.client.generate(schema, template, payload, stage)
        result.setdefault("provenance", []).append(meta)
        return model

    def run(self, initial: dict[str, Any], saver: Any, run_id: str) -> dict[str, Any]:
        graph = StateGraph(FlowState)
        for name in ("prepare", "analysis", "outline", "shots", "review"):
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, "prepare")
        for left, right in zip(
            ("prepare", "analysis", "outline", "shots"), ("analysis", "outline", "shots", "review")
        ):
            graph.add_edge(left, right)
        graph.add_edge("review", END)
        app = graph.compile(checkpointer=saver)
        state = app.invoke(
            {"result": initial}, {"configurable": {"thread_id": run_id}, "recursion_limit": 100}
        )
        return dict(state["result"])

    def prepare(self, state: FlowState) -> FlowState:
        result = dict(state["result"])
        if "paragraphs" not in result:
            result.update(
                schema_version="1.0",
                prompt_template_version="1.0",
                paragraphs=[p.model_dump() for p in paragraphs(self.document["text"])],
                provenance=[],
                generation_status="not_submitted",
            )
            self.save(result, "prepare")
        return {"result": result}

    def _paragraph_chunks(self, items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        chunks: list[list[dict[str, Any]]] = [[]]
        for paragraph in items:
            single = self._analysis_payload([paragraph], [], 0)
            if not self._within_budget(Analysis, "analysis", single):
                raise AppError(
                    "paragraph_too_long", "某一段超过上下文预算，请增大预算或按语义换行。"
                )
            if chunks[-1] and not self._within_budget(
                Analysis,
                "analysis",
                self._analysis_payload(chunks[-1] + [paragraph], [], len(chunks) - 1),
            ):
                chunks.append([])
            chunks[-1].append(paragraph)
        return [chunk for chunk in chunks if chunk]

    def analysis(self, state: FlowState) -> FlowState:
        result = dict(state["result"])
        if "analysis" in result:
            return {"result": result}
        covered = list(result.get("analysis_covered") or [])
        covered_ids = set(covered)
        remaining = [p for p in result["paragraphs"] if p["paragraph_id"] not in covered_ids]
        parts = result.setdefault("analysis_parts", [])
        for chunk in self._paragraph_chunks(remaining):
            queue = [chunk]
            while queue:
                current = queue.pop(0)
                try:
                    extracted = self.call(
                        result,
                        Analysis,
                        "analysis",
                        self._analysis_payload(
                            current,
                            parts[-1].get("characters", []) if parts else [],
                            len(parts),
                        ),
                    )
                except AppError as exc:
                    if exc.code == "llm_truncated" and len(current) > 1:
                        # The whole chunk exceeded the output cap. Keep paragraph order and retry halves.
                        middle = len(current) // 2
                        queue[:0] = [current[:middle], current[middle:]]
                        continue
                    if exc.code == "llm_truncated":
                        raise AppError(
                            "llm_truncated",
                            "单段分析仍超过输出上限。请在模型配置提高「最大输出 / token」，或把这一段拆短后再试。",
                        ) from None
                    raise
                parts.append(extracted.model_dump())
                covered.extend(item["paragraph_id"] for item in current)
                result["analysis_covered"] = covered
                self.save(result, f"analysis {len(parts)}")
        if len(parts) == 1:
            result["analysis"] = parts[0]
        else:
            merged = self.call(
                result,
                Analysis,
                "analysis",
                {
                    "merge_parts": parts,
                    "instruction": "统一别名与角色ID，去重事件，保留全部必须事件和原文编号。",
                },
            )
            result["analysis"] = merged.model_dump()
        self.save(result, "analysis")
        return {"result": result}

    def outline(self, state: FlowState) -> FlowState:
        result = dict(state["result"])
        if "outline" not in result:
            outline = self.call(
                result,
                Outline,
                "outline",
                {
                    "analysis": result["analysis"],
                    "style": self.document["style"],
                    "preference": self.document["preference"],
                },
            )
            if not outline.scenes:
                raise AppError("empty_outline", "模型没有输出场景，请检查章节或重试本阶段。")
            result["outline"] = outline.model_dump()
            self.save(result, "outline")
        return {"result": result}

    def _shot_groups(
        self, events: list[dict[str, Any]], paragraphs: list[dict[str, Any]]
    ) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
        if len(events) > 1:
            middle = len(events) // 2
            return [
                self._events_slice(events[:middle], paragraphs),
                self._events_slice(events[middle:], paragraphs),
            ]
        if len(paragraphs) > 1:
            middle = len(paragraphs) // 2
            return [(events, paragraphs[:middle]), (events, paragraphs[middle:])]
        return []

    @staticmethod
    def _events_slice(
        events: list[dict[str, Any]], paragraphs: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        ids = {paragraph_id for event in events for paragraph_id in event["source_paragraph_ids"]}
        return events, [item for item in paragraphs if item["paragraph_id"] in ids]

    def shots(self, state: FlowState) -> FlowState:
        result = dict(state["result"])
        batches = result.setdefault("shot_batches", {})
        for scene in result["outline"]["scenes"]:
            scene_id = scene["scene_id"]
            if scene_id in batches:
                continue
            events = [
                event
                for event in result["analysis"]["events"]
                if event["event_id"] in scene["event_ids"]
            ]
            ids = {paragraph_id for event in events for paragraph_id in event["source_paragraph_ids"]}
            scene_paragraphs = [item for item in result["paragraphs"] if item["paragraph_id"] in ids]
            queue = [(events, scene_paragraphs if events else list(result["paragraphs"]))]
            collected: list[dict[str, Any]] = []
            while queue:
                current_events, current_paragraphs = queue.pop(0)
                previous = [shot for batch in batches.values() for shot in batch]
                previous.extend(collected)
                try:
                    batch = self.call(
                        result,
                        ShotBatch,
                        "shots",
                        {
                            "scene": scene,
                            "events": current_events,
                            "paragraphs": current_paragraphs,
                            "characters": result["analysis"]["characters"],
                            "visual_profiles": self.document["visual_profiles"],
                            "style": self.document["style"],
                            "aspect_ratio": self.document["aspect_ratio"],
                            "previous_shot": previous[-1] if previous else None,
                            "instruction": (
                                f"全局order从{len(previous) + 1}开始。镜头ID以{scene_id}_开头。"
                                "只覆盖本次 events 与 paragraphs，不补写未提供的事件。"
                                "按叙事需要决定镜头数量，不凑数，提示词为视频语义草案。"
                            ),
                        },
                    )
                except AppError as exc:
                    smaller = (
                        self._shot_groups(current_events, current_paragraphs)
                        if exc.code == "llm_truncated"
                        else []
                    )
                    if smaller:
                        queue[:0] = smaller
                        continue
                    if exc.code == "llm_truncated":
                        raise AppError(
                            "llm_truncated",
                            "这一场的镜头输出仍超过上限。请在模型配置提高「最大输出 / token」后再重试。",
                        ) from None
                    raise
                if not batch.shots:
                    raise AppError("empty_shots", "模型没有输出镜头，已保存此前结果。")
                collected.extend(shot.model_dump() for shot in batch.shots)
            batches[scene_id] = collected
            self.save(result, f"shots {scene_id}")
        result["shots"] = [shot for batch in batches.values() for shot in batch]
        self.save(result, "shots")
        return {"result": result}

    def review(self, state: FlowState) -> FlowState:
        result = dict(state["result"])
        if "review" not in result:
            review_parts = result.setdefault("review_parts", {})
            for scene in result["outline"]["scenes"]:
                scene_id = scene["scene_id"]
                if scene_id in review_parts:
                    continue
                scene_shots = [s for s in result["shots"] if s["scene_id"] == scene_id]
                issues: list[str] = []
                for group in self._review_groups(result, scene, scene_shots):
                    review = self.call(
                        result,
                        Review,
                        "review",
                        self._review_payload(result, scene, group),
                    )
                    issues.extend(review.issues)
                review_parts[scene_id] = {"issues": issues}
                self.save(result, f"review {scene_id}")
            result["review"] = {
                "issues": [issue for part in review_parts.values() for issue in part["issues"]]
            }
        result["validation_issues"] = validate_storyboard(
            Analysis(**result["analysis"]),
            Outline(**result["outline"]),
            [Shot(**s) for s in result["shots"]],
            [SourceParagraph(**p) for p in result["paragraphs"]],
        )
        self.save(result, "review")
        return {"result": result}

    def _review_payload(
        self, result: dict[str, Any], scene: dict[str, Any], shots: list[dict[str, Any]]
    ) -> dict[str, Any]:
        analysis = result["analysis"]
        event_ids = set(scene.get("event_ids") or [])
        events = [event for event in analysis.get("events", []) if event["event_id"] in event_ids]
        character_ids = {cid for shot in shots for cid in shot.get("character_ids") or []}
        characters = [
            character
            for character in analysis.get("characters", [])
            if not character_ids or character["character_id"] in character_ids
        ]
        # The whole chapter analysis repeats on every scene and pushed later scenes over budget.
        return {
            "analysis": {
                "summary": analysis.get("summary", ""),
                "core_conflict": analysis.get("core_conflict", ""),
                "characters": characters,
                "events": events,
                "uncertainties": analysis.get("uncertainties") or [],
                "relations": analysis.get("relations") or [],
            },
            "scene": scene,
            "shots": shots,
        }

    def _review_groups(
        self, result: dict[str, Any], scene: dict[str, Any], shots: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        payload = self._review_payload(result, scene, shots)
        if not shots or self._within_budget(Review, "review", payload):
            return [shots]
        groups: list[list[dict[str, Any]]] = [[]]
        for shot in shots:
            if groups[-1] and not self._within_budget(
                Review, "review", self._review_payload(result, scene, groups[-1] + [shot])
            ):
                groups.append([])
            groups[-1].append(shot)
        return groups

    def _analysis_payload(
        self, chunk: list[dict[str, Any]], existing: list[Any], index: int
    ) -> dict[str, Any]:
        return {
            "paragraphs": chunk,
            "optional_context": self.document["context"],
            "existing_characters": existing,
            "id_instruction": (
                f"事件ID以 B{index + 1}_ 开头，角色沿用已知ID或以 C{index + 1}_ 开头。"
            ),
        }
