"""Private file credentials shared by the web process and text worker."""
import json
import os
import tempfile
from pathlib import Path
from threading import Lock

from novel_anime.core.settings import get_settings

_LOCK = Lock()


def credential_path() -> Path:
    return get_settings().data_dir / "credentials.json"


def read_credentials() -> dict[str, str]:
    path = credential_path()
    return json.loads(path.read_text()) if path.exists() else {}


def resolve_key(name: str) -> str:
    # An explicit empty entry suppresses legacy environment credentials after clearing.
    stored = read_credentials()
    return stored.get(name, os.environ.get(name, "")).strip()


def store_key(name: str, value: str) -> None:
    with _LOCK:
        stored = read_credentials()
        stored[name] = value.strip()
        path = credential_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".credentials-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(stored, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
