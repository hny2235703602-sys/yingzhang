"""Deterministic checks; narrative correctness still needs a separate review."""

from collections import Counter
from collections.abc import Sequence

from novel_anime.domain.models import Analysis, Outline, Shot, SourceParagraph


def validate_storyboard(
    analysis: Analysis,
    outline: Outline,
    shots: Sequence[Shot],
    paragraphs: Sequence[SourceParagraph],
) -> list[str]:
    """Return all structural/reference/coverage issues without changing inputs."""
    issues: list[str] = []

    def unique(values: list[str], label: str) -> set[str]:
        duplicates = sorted(key for key, count in Counter(values).items() if count > 1)
        if duplicates:
            issues.append(f"{label}编号重复: {', '.join(duplicates)}")
        return set(values)

    def references(values: Sequence[str], allowed: set[str], label: str) -> None:
        missing = sorted(set(values) - allowed)
        if missing:
            issues.append(f"{label}引用不存在: {', '.join(missing)}")
        if len(values) != len(set(values)):
            issues.append(f"{label}含重复引用")

    paragraph_ids = unique([item.paragraph_id for item in paragraphs], "原文段落")
    character_ids = unique([item.character_id for item in analysis.characters], "角色")
    event_ids = unique([item.event_id for item in analysis.events], "事件")
    scene_ids = unique([item.scene_id for item in outline.scenes], "场景")
    shot_ids = unique([item.shot_id for item in shots], "镜头")
    for character in analysis.characters:
        references(
            character.source_paragraph_ids, paragraph_ids, f"角色 {character.character_id} 原文"
        )
        if not character.source_paragraph_ids:
            issues.append(f"角色 {character.character_id} 缺少原文依据")
    for event in analysis.events:
        references(event.source_paragraph_ids, paragraph_ids, f"事件 {event.event_id} 原文")
        if not event.source_paragraph_ids:
            issues.append(f"事件 {event.event_id} 缺少原文依据")
    scene_events = {scene.scene_id: set(scene.event_ids) for scene in outline.scenes}
    for scene in outline.scenes:
        references(scene.event_ids, event_ids, f"场景 {scene.scene_id} 事件")
    orders = [shot.order for shot in shots]
    if orders != list(range(1, len(shots) + 1)):
        issues.append("镜头顺序须从 1 开始连续递增且与列表顺序一致")
    shot_map = {shot.shot_id: shot for shot in shots}
    scene_position = {scene.scene_id: i for i, scene in enumerate(outline.scenes)}
    last_scene_position = -1
    for shot in shots:
        label = f"镜头 {shot.shot_id}"
        references([shot.scene_id], scene_ids, f"{label} 场景")
        references(shot.source_paragraph_ids, paragraph_ids, f"{label} 原文")
        references(shot.source_event_ids, event_ids, f"{label} 事件")
        references(shot.character_ids, character_ids, f"{label} 角色")
        references(shot.depends_on_shot_ids, shot_ids, f"{label} 依赖")
        if not shot.source_paragraph_ids:
            issues.append(f"{label} 缺少原文依据")
        if shot.scene_id in scene_position:
            current = scene_position[shot.scene_id]
            if current < last_scene_position:
                issues.append(f"{label} 场景顺序与大纲不一致")
            last_scene_position = max(current, last_scene_position)
            references(shot.source_event_ids, scene_events[shot.scene_id], f"{label} 所属场景事件")
        for dependency in shot.depends_on_shot_ids:
            if dependency in shot_map and shot_map[dependency].order >= shot.order:
                issues.append(f"{label} 依赖 {dependency} 必须先于当前镜头")
    covered = {event for shot in shots for event in shot.source_event_ids}
    outlined = {event for scene in outline.scenes for event in scene.event_ids}
    for event in analysis.events:
        if event.importance == "must":
            if event.event_id not in outlined:
                issues.append(f"必须保留事件 {event.event_id} 未被大纲覆盖")
            if event.event_id not in covered:
                issues.append(f"必须保留事件 {event.event_id} 未被镜头覆盖")

    # Iterative depth-first traversal also handles long chapters without recursion limits.
    colors: dict[str, int] = {}
    for root in shot_ids:
        if colors.get(root, 0):
            continue
        stack = [(root, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                colors[node] = 2
                continue
            if colors.get(node) == 2:
                continue
            if colors.get(node) == 1:
                issues.append(f"镜头依赖存在循环: {node}")
                continue
            colors[node] = 1
            stack.append((node, True))
            for dependency in reversed(shot_map[node].depends_on_shot_ids):
                if dependency in shot_map:
                    stack.append((dependency, False))
    return issues
