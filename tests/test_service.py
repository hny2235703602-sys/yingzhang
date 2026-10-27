from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from novel_anime.application.service import StudioService
from novel_anime.core.errors import AppError
from novel_anime.core.settings import ROOT
from novel_anime.domain.models import ModelProfile, VisualProfile
from novel_anime.persistence.database import ResultVersionRow, RunRow
from novel_anime.workers.main import Worker


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> StudioService:
    monkeypatch.setenv("YINGZHANG_DATA_DIR", str(tmp_path))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")
    return StudioService()


def create_run(service: StudioService, key: str = "submit-1", **changes: Any) -> str:
    work_id = service.create_work("示例作品")
    chapter_id = service.save_chapter(work_id, "第一章", "少年推开殿门。", "")
    options: dict[str, Any] = {
        "profile": ModelProfile(
            provider="local",
            protocol="chat_completions",
            base_url="http://localhost:1234/v1",
            model="test",
            api_key_env="",
        ).model_dump(),
        "style": "二维动画",
        "aspect_ratio": "16:9",
        "preference": "",
        "idempotency_key": key,
    }
    options.update(changes)
    return service.create_run(chapter_id, **options)


def test_create_run_and_idempotency(service: StudioService) -> None:
    run_id = create_run(service)
    run = service.get_run(run_id)
    assert run["status"] == "pending"
    assert run["document"]["text"] == "少年推开殿门。"
    args = (run["chapter_id"], run["profile"], "二维动画", "16:9", "", "submit-1")
    assert service.create_run(*args) == run_id
    with pytest.raises(AppError) as caught:
        service.create_run(run["chapter_id"], run["profile"], "写实", "16:9", "", "submit-1")
    assert caught.value.code == "idempotency_conflict"
    assert len(service.list_runs(run["chapter_id"])) == 1
    # Deliberate redo is a new attempt even when all adaptation inputs are identical.
    redo = service.create_run(*args[:-1], "submit-2")
    assert redo != run_id


def test_chapter_stale_save_does_not_overwrite(service: StudioService) -> None:
    work = service.create_work("作品")
    chapter = service.save_chapter(work, "第一章", "旧正文", "")
    service.save_chapter(work, "更新", "新正文", "前情", chapter, 1)
    with pytest.raises(AppError) as caught:
        service.save_chapter(work, "过期修改", "错误正文", "", chapter, 1)
    assert caught.value.code == "version_conflict"
    saved = service.get_chapter(chapter)
    assert saved["revision"] == 2
    assert saved["text"] == "新正文"


def test_visual_versions_are_immutable_and_work_scoped(service: StudioService) -> None:
    work = service.create_work("作品")
    other = service.create_work("另一作品")
    first = VisualProfile(character_id="hero", name="少年", appearance="黑发", costume="蓝袍")
    service.save_visual_profile(work, first.model_dump())
    with pytest.raises(AppError) as caught:
        service.save_visual_profile(work, first.model_dump())
    assert caught.value.code == "version_conflict"
    newer = first.model_copy(update={"version": 2, "costume": "白袍"})
    service.save_visual_profile(work, newer.model_dump())
    assert service.list_visual_profiles(work) == [newer.model_dump()]
    assert service.list_visual_profiles(other) == []


def test_character_image_adoption_versions_profile_and_protects_assets(
    service: StudioService,
) -> None:
    work = service.create_work("作品")
    profile = VisualProfile(character_id="hero", name="少年", appearance="黑发", costume="蓝袍")
    service.save_visual_profile(work, profile.model_dump())
    service.add_character_image(
        work,
        {
            "asset_id": "asset_001",
            "work_id": work,
            "character_id": "hero",
            "costume_version": "costume_01",
            "purpose": "candidate",
            "filename": "asset_001.png",
            "mime_type": "image/png",
            "size_bytes": 100,
            "width": 64,
            "height": 64,
            "sha256": "a" * 64,
            "profile_version": 1,
            "created_at": "2026-10-27T00:00:00+00:00",
        },
    )
    assert service.adopt_character_image(work, "asset_001", "primary", 1) == 2
    current = service.list_visual_profiles(work)[0]
    assert current["version"] == 2
    assert current["reference_assets"] == ["asset_001"]
    assert service.list_character_images(work)[0]["profile_version"] == 2
    with pytest.raises(AppError) as caught:
        service.delete_character_image(work, "asset_001")
    assert caught.value.code == "invalid_state"
    with pytest.raises(AppError) as conflict:
        service.adopt_character_image(work, "asset_001", "side", 1)
    assert conflict.value.code == "version_conflict"


def test_character_image_records_are_work_scoped(service: StudioService) -> None:
    first = service.create_work("第一部")
    second = service.create_work("第二部")
    profile = VisualProfile(character_id="hero", name="少年", appearance="黑发", costume="蓝袍")
    service.save_visual_profile(first, profile.model_dump())
    with pytest.raises(AppError) as caught:
        service.adopt_character_image(second, "asset_missing", "primary", 1)
    assert caught.value.code == "not_found"


def test_shot_save_revision_and_archive(service: StudioService) -> None:
    from test_models import storyboard

    analysis, outline, shots, paragraphs = storyboard()
    original = {
        "analysis": analysis.model_dump(),
        "outline": outline.model_dump(),
        "shots": [s.model_dump() for s in shots],
        "paragraphs": [p.model_dump() for p in paragraphs],
    }
    run_id = create_run(service)
    with service.sessions.begin() as session:
        row = session.get(RunRow, run_id)
        row.status = "succeeded"
        row.result = original
    edited = shots[0].model_copy(update={"video_prompt": "更新的视觉描述"}).model_dump()
    service.save_run_shots(run_id, [edited], 1)
    saved = service.get_run(run_id)
    assert saved["revision"] == 2
    assert saved["status"] == "review_required"
    assert saved["result"]["shots"] == [edited]
    with service.sessions() as session:
        archived = session.scalar(select(ResultVersionRow).where(ResultVersionRow.run_id == run_id))
        assert archived.result == original
    with pytest.raises(AppError) as caught:
        service.save_run_shots(run_id, [edited], 1)
    assert caught.value.code == "version_conflict"


def test_multiple_workers_claim_at_most_one_run(service: StudioService) -> None:
    runs = {create_run(service, "a"), create_run(service, "b")}
    workers = [Worker(), Worker()]
    barrier = Barrier(2)

    def claim(worker: Worker) -> str | None:
        barrier.wait(timeout=5)
        return worker.claim()

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, workers))
    assert len([claim for claim in claims if claim is not None]) == 1
    claimed = next(claim for claim in claims if claim is not None)
    assert claimed in runs
    assert len({service.get_run(run)["owner"] for run in runs} - {None}) == 1


def test_expired_lease_requires_explicit_retry(service: StudioService) -> None:
    run_id = create_run(service)
    worker = Worker()
    assert worker.claim() == run_id
    with service.sessions.begin() as session:
        row = session.get(RunRow, run_id)
        row.lease_until = "2000-01-01T00:00:00+00:00"
        row.result = {"saved_stage": "analysis"}
    restarted = Worker()
    assert restarted.claim() is None
    run = service.get_run(run_id)
    assert run["status"] == "unknown"
    assert run["owner"] is None
    assert run["result"] == {"saved_stage": "analysis"}
    assert "计费" in run["error_message"]
    service.retry_run(run_id)
    assert restarted.claim() == run_id


def test_pause_pending_and_resume(service: StudioService) -> None:
    run_id = create_run(service)
    service.pause_run(run_id)
    assert service.get_run(run_id)["status"] == "paused"
    worker = Worker()
    assert worker.claim() is None
    assert service.get_run(run_id)["status"] == "paused"
    service.resume_run(run_id)
    assert worker.claim() == run_id
    service.pause_run(run_id)
    service.resume_run(run_id)
    assert service.get_run(run_id)["status"] == "running"


def test_stop_unclaimed_task_needs_no_worker(service: StudioService) -> None:
    run_id = create_run(service)
    service.pause_run(run_id)
    service.stop_run(run_id)
    assert service.get_run(run_id)["status"] == "stopped"
    assert Worker().claim() is None
