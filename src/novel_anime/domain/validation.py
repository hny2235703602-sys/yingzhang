"""Deterministic script checks; narrative quality still needs human review."""

from collections import Counter
from collections.abc import Sequence

from novel_anime.domain.models import SourceParagraph, StoryScript

MAX_SHOT_SECONDS = 15


def validate_script(script: StoryScript, paragraphs: Sequence[SourceParagraph]) -> list[str]:
    """Check structure, references, pacing and paragraph coverage without changing inputs."""
    issues: list[str] = []
    if not script.shots:
        issues.append("脚本没有任何分镜")

    duplicates = sorted(
        key for key, count in Counter(shot.shot_id for shot in script.shots).items() if count > 1
    )
    if duplicates:
        issues.append(f"镜头编号重复: {', '.join(duplicates)}")
    repeated = sorted(
        key
        for key, count in Counter(person.character_id for person in script.characters).items()
        if count > 1
    )
    if repeated:
        issues.append(f"角色编号重复: {', '.join(repeated)}")

    if [shot.order for shot in script.shots] != list(range(1, len(script.shots) + 1)):
        issues.append("镜头顺序须从 1 开始连续递增且与列表顺序一致")

    paragraph_ids = [item.paragraph_id for item in paragraphs]
    if len(paragraph_ids) != len(set(paragraph_ids)):
        issues.append("原文段落编号重复")
    known_paragraphs = set(paragraph_ids)
    people = {person.character_id: person for person in script.characters}
    covered: set[str] = set()
    for shot in script.shots:
        label = f"镜头 {shot.shot_id}"
        missing = sorted(set(shot.character_ids) - set(people))
        if missing:
            issues.append(f"{label} 角色引用不存在: {', '.join(missing)}")
        if len(shot.character_ids) != len(set(shot.character_ids)):
            issues.append(f"{label} 含重复角色引用")
        if any(
            not people[item].appearance.strip() for item in shot.character_ids if item in people
        ):
            issues.append(f"{label} 缺少角色外观")
        unknown = sorted(set(shot.source_paragraph_ids) - known_paragraphs)
        if unknown:
            issues.append(f"{label} 原文引用不存在: {', '.join(unknown)}")
        if not shot.source_paragraph_ids:
            issues.append(f"{label} 缺少原文依据")
        covered.update(shot.source_paragraph_ids)
        if shot.duration_seconds > MAX_SHOT_SECONDS:
            issues.append(
                f"{label} 时长 {shot.duration_seconds:g} 秒，超过 {MAX_SHOT_SECONDS} 秒建议拆分"
            )

    missing_coverage = [item for item in paragraph_ids if item not in covered]
    if missing_coverage:
        issues.append(f"原文段落未被写入脚本: {', '.join(missing_coverage[:10])}")
    return issues
