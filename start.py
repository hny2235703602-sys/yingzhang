"""Launch UI and independent worker with a shared environment; install separately with uv sync."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent


def main() -> None:
    os.chdir(ROOT)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    worker = subprocess.Popen([sys.executable, "-m", "novel_anime.workers.main"], env=environment)
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                "app.py",
                "--server.address",
                "127.0.0.1",
                "--server.port",
                "8501",
            ],
            env=environment,
            check=True,
        )
    except KeyboardInterrupt:
        pass
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker.kill()


if __name__ == "__main__":
    main()
