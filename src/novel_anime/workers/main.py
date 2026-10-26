from __future__ import annotations

import argparse
import copy
import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import select, update

from novel_anime.core.errors import AppError
from novel_anime.core.settings import get_settings
from novel_anime.domain.models import ModelProfile
from novel_anime.persistence.database import RunRow, now, sessions
from novel_anime.workflows.adaptation import AdaptationFlow


class Paused(Exception):
    pass


class Worker:
    """Single-run database claim with heartbeat; stale runs require explicit reconciliation."""

    def __init__(self) -> None:
        self.sessions = sessions()
        self.settings = get_settings()
        self.owner = uuid4().hex

    def deadline(self) -> str:
        return (datetime.now(UTC) + timedelta(seconds=self.settings.lease_seconds)).isoformat()

    def recover_expired(self) -> None:
        with self.sessions.begin() as session:
            session.execute(
                update(RunRow)
                .where(RunRow.owner.is_not(None), RunRow.lease_until < now())
                .values(
                    status="unknown",
                    owner=None,
                    lease_until=None,
                    error_message="执行器失联，最后一次调用可能已计费。"
                    "请核对后手动重试；成功保存的阶段会保留。",
                    updated_at=now(),
                )
            )
            session.execute(
                update(RunRow)
                .where(RunRow.status == "pause_requested", RunRow.owner.is_(None))
                .values(status="paused", updated_at=now())
            )

    def claim(self) -> str | None:
        self.recover_expired()
        with self.sessions.begin() as session:
            if session.scalar(select(RunRow.id).where(RunRow.owner.is_not(None)).limit(1)):
                return None
            candidate = (
                select(RunRow.id).where(RunRow.status == "pending").limit(1).scalar_subquery()
            )
            row = session.execute(
                update(RunRow)
                .where(
                    RunRow.id == candidate,
                    RunRow.status == "pending",
                    ~select(RunRow.id).where(RunRow.owner.is_not(None)).exists(),
                )
                .values(
                    status="running",
                    owner=self.owner,
                    lease_until=self.deadline(),
                    updated_at=now(),
                )
                .returning(RunRow.id)
            ).first()
            return str(row[0]) if row else None

    def heartbeat(self, run_id: str, stop: threading.Event) -> None:
        while not stop.wait(10):
            with self.sessions.begin() as session:
                session.execute(
                    update(RunRow)
                    .where(RunRow.id == run_id, RunRow.owner == self.owner)
                    .values(lease_until=self.deadline())
                )

    def process(self, run_id: str) -> None:
        stop = threading.Event()
        thread = threading.Thread(target=self.heartbeat, args=(run_id, stop), daemon=True)
        thread.start()

        def boundary(stage: str) -> None:
            with self.sessions.begin() as session:
                row = session.get(RunRow, run_id)
                if row is None or row.owner != self.owner:
                    raise AppError("lease_lost", "任务租约已丢失，已停止后续调用。")
                if row.status == "pause_requested":
                    raise Paused()
                row.stage = stage
                row.updated_at = now()

        def save(result: dict[str, Any], stage: str) -> None:
            with self.sessions.begin() as session:
                row = session.get(RunRow, run_id)
                if row is None or row.owner != self.owner:
                    raise AppError("lease_lost", "任务租约已丢失。")
                row.result = copy.deepcopy(result)
                row.stage = stage
                row.updated_at = now()

        try:
            with self.sessions() as session:
                row = session.get(RunRow, run_id)
                if row is None or row.owner != self.owner:
                    return
                document, profile, initial = row.document, row.profile, row.result
            flow = AdaptationFlow(document, ModelProfile(**profile), save, boundary)
            with SqliteSaver.from_conn_string(
                str(self.settings.data_dir / "checkpoints.db")
            ) as saver:
                result = flow.run(initial, saver, run_id)
            issues = list(result.get("validation_issues", []))
            issues.extend(result.get("review", {}).get("issues", []))
            with self.sessions.begin() as session:
                row = session.get(RunRow, run_id)
                if row and row.owner == self.owner:
                    row.status = "review_required" if issues else "succeeded"
                    row.validation_issues = issues
                    row.updated_at = now()
        except Paused:
            self.finish_error(run_id, "paused", None)
        except AppError as exc:
            self.finish_error(run_id, "failed", f"{exc.code} · {exc.message}")
        except Exception:
            # Raw provider payloads and novel content must never reach diagnostics.
            self.finish_error(run_id, "failed", "internal_error · 本地执行异常，已保留阶段结果。")
        finally:
            stop.set()
            thread.join(timeout=1)
            with self.sessions.begin() as session:
                session.execute(
                    update(RunRow)
                    .where(RunRow.id == run_id, RunRow.owner == self.owner)
                    .values(owner=None, lease_until=None)
                )

    def finish_error(self, run_id: str, status: str, message: str | None) -> None:
        with self.sessions.begin() as session:
            session.execute(
                update(RunRow)
                .where(RunRow.id == run_id, RunRow.owner == self.owner)
                .values(status=status, error_message=message, updated_at=now())
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Process at most one pending run")
    args = parser.parse_args()
    worker = Worker()
    print("映章文本执行器已启动，等待任务。", flush=True)
    stop = threading.Event()
    try:
        while True:
            run_id = worker.claim()
            if run_id:
                worker.process(run_id)
            if args.once:
                break
            if not run_id:
                stop.wait(worker.settings.poll_seconds)
    except KeyboardInterrupt:
        print("执行器已停止。")


if __name__ == "__main__":
    main()
