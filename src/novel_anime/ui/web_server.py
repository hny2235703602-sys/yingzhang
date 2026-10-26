"""Same-origin HTML interface using the existing service and independent worker."""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from novel_anime.application.service import StudioService
from novel_anime.core.credentials import resolve_key, store_key
from novel_anime.core.errors import AppError
from novel_anime.domain.models import ModelProfile
from novel_anime.persistence.database import ConfigRow, WorkRow, now
from novel_anime.ui.export import safe_payload

STATIC = Path(__file__).with_name("web")
LOG = logging.getLogger(__name__)


def snapshot(service: StudioService) -> dict:
    with service.sessions() as session:
        row = session.get(ConfigRow, "web_metadata")
        metadata = row.value if row else {}
    works = []
    for work in service.list_works():
        extra = metadata.get(work["id"], {})
        works.append(
            {
                **extra,
                **work,
                "style": extra.get("style", "国风动画"),
                "updated": extra.get("updated", ""),
                "characters": service.list_visual_profiles(work["id"]),
                "chapters": service.list_chapters(work["id"]),
            }
        )
    profiles = [
        {
            **p,
            "id": key,
            "name": p["provider"],
            "url": p["base_url"],
            "env": p["api_key_env"],
            "tokens": p["max_output_tokens"],
            "key_configured": bool(resolve_key(p["api_key_env"])),
        }
        for key, p in service.load_profiles().items()
    ]
    from novel_anime.core.settings import ROOT

    saved = service._load_config("prompt_templates")
    templates = {
        stage: saved.get(stage) or (ROOT / "prompts" / f"{stage}.txt").read_text(encoding="utf-8")
        for stage in ("analysis", "outline", "shots", "review")
    }
    return {"novels": works, "profiles": profiles, "templates": templates}


def dispatch(service: StudioService, action: str, body: dict) -> dict:
    if action == "prompts":
        templates = {}
        for stage in ("analysis", "outline", "shots", "review"):
            value = body.get(stage)
            if not isinstance(value, str) or not value.strip() or len(value) > 20000:
                raise AppError("invalid_input", "每个阶段请填写 1 到 20000 字的提示词。")
            templates[stage] = value.strip()
        service._save_config("prompt_templates", templates)
        return {"saved": True}
    if action == "work":
        title = str(body.get("title", "")).strip()
        if not title or len(title) > 200:
            raise AppError("invalid_input", "请填写 200 字以内的作品名称。")
        work_id = body.get("id")
        with service.sessions.begin() as session:
            if work_id:
                row = session.get(WorkRow, work_id)
                if row is None:
                    raise AppError("not_found", "未找到作品。")
                row.title = title
            else:
                work_id = uuid4().hex
                session.add(WorkRow(id=work_id, title=title))
            row = session.get(ConfigRow, "web_metadata")
            metadata = dict(row.value) if row else {}
            cover = str(body.get("cover", ""))
            if cover and not cover.startswith(
                ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
            ):
                raise AppError("invalid_input", "封面格式不支持。")
            if len(cover) > 2_900_000:
                raise AppError("invalid_input", "封面不能超过 2 MB。")
            metadata[work_id] = {
                "style": str(body.get("style", "国风动画"))[:100],
                "cover": cover,
                "updated": now(),
            }
            session.merge(ConfigRow(key="web_metadata", value=metadata))
        return {"id": work_id}
    if action == "chapter":
        ident = service.save_chapter(
            body["work_id"],
            body["title"],
            body["text"],
            body.get("context", ""),
            body.get("id"),
            body.get("revision"),
        )
        return {"id": ident}
    if action == "simple-profile":
        from novel_anime.integrations.language_models.client import LLMClient

        ident = body.get("id") or "online"
        existing = {
            key: value
            for key, value in service.load_profiles().get(ident, {}).items()
            if key in ModelProfile.model_fields
        }
        url = urlparse(str(body.get("url", "")))
        if (
            url.scheme != "https"
            or not url.netloc
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise AppError("invalid_input", "请填写不含账号、查询参数的 HTTPS API 地址。")
        timeout = float(body.get("timeout", 120))
        if not 10 <= timeout <= 600:
            raise AppError("invalid_input", "超时范围为 10 到 600 秒。")
        max_output = int(existing.get("max_output_tokens", 4000))
        context_budget = int(body.get("context_budget", existing.get("context_budget", 16000)))
        if context_budget <= max_output:
            raise AppError("invalid_input", f"上下文预算须大于最大输出 {max_output} token。")
        profile = ModelProfile(
            **{
                **existing,
                "profile_id": ident,
                "provider": existing.get("provider", "线上模型"),
                "protocol": "chat_completions",
                "base_url": body["url"].rstrip("/"),
                "model": body.get("model", "catalog"),
                "timeout": timeout,
                "temperature": body.get("temperature", 1),
                "context_budget": context_budget,
                "enabled": body.get("enabled", True),
                "api_key_env": existing.get("api_key_env") or f"YINGZHANG_KEY_{ident}",
            }
        )
        supplied = body.get("key", "")
        if not isinstance(supplied, str) or len(supplied) > 4096 or "\n" in supplied:
            raise AppError("invalid_input", "API Key 格式不正确。")
        if body.get("list_models"):
            # Transient key used only for this catalog request; never persisted by listing.
            models = LLMClient(profile, api_key=supplied or None).list_models()
            return {"models": models}
        service.save_profile(profile.model_dump())
        if body.get("clear_key"):
            store_key(profile.api_key_env, "")
        elif supplied.strip():
            store_key(profile.api_key_env, supplied)
        return {"id": profile.profile_id}
    if action == "profile":
        profile = ModelProfile(**body)
        service.save_profile(profile.model_dump())
        return {"id": profile.profile_id}
    if action == "run":
        profiles = service.load_profiles()
        profile = profiles.get(body.get("profile_id"))
        if not profile:
            raise AppError("invalid_input", "请先保存并选择模型配置。")
        if not profile.get("enabled", True):
            raise AppError("model_disabled", "模型已关闭，请在设置中启用。")
        if not profile.get("api_key_env") or not resolve_key(profile["api_key_env"]):
            raise AppError("missing_api_key", "请在模型配置中填写 API Key 并保存设置。")
        ident = service.create_run(
            body["chapter_id"],
            profile,
            body.get("style", "国风动画"),
            body.get("aspect_ratio", "16:9"),
            body.get("preference", ""),
            body["idempotency_key"],
        )
        return {"id": ident}
    if action in {"pause", "resume", "retry"}:
        getattr(service, action + "_run")(body["id"])
        return {"id": body["id"]}
    raise AppError("invalid_input", "不支持的操作。")


class Handler(BaseHTTPRequestHandler):
    def send(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(data)

    def json(self, status: int, value: dict | list) -> None:
        profiles = StudioService().load_profiles()
        secrets = tuple(resolve_key(p.get("api_key_env", "")) for p in profiles.values())
        self.send(
            status,
            json.dumps(
                safe_payload(value, tuple(s for s in secrets if s)), ensure_ascii=False
            ).encode(),
            "application/json; charset=utf-8",
        )

    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            service = StudioService()
            if path == "/api/state":
                self.json(200, snapshot(service))
            elif path.startswith("/api/runs/"):
                self.json(200, service.list_runs(path.removeprefix("/api/runs/")))
            elif path == "/health":
                self.send(200, b"ok", "text/plain")
            elif path in {"/", "/index.html", "/cloud.js"}:
                name = "cloud.js" if path == "/cloud.js" else "index.html"
                mime = "text/javascript" if name.endswith(".js") else "text/html"
                self.send(200, (STATIC / name).read_bytes(), mime + "; charset=utf-8")
            else:
                self.send(404, b"Not found", "text/plain")
        except Exception:
            LOG.exception("Web read failed")
            self.send(500, b'{"error":"Unable to load data"}', "application/json")

    def do_POST(self) -> None:
        # No CORS; block browser cross-site writes to a loopback/SSH-only service.
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self.send(403, b'{"error":"Origin rejected"}', "application/json")
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.send(415, b"JSON required", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4_000_000:
                self.send(413, b"Request too large", "text/plain")
                return
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError()
            self.json(
                200, dispatch(StudioService(), urlparse(self.path).path.removeprefix("/api/"), body)
            )
        except AppError as exc:
            self.json(
                409 if exc.code == "version_conflict" else 400,
                {"error": exc.message, "code": exc.code},
            )
        except (ValueError, KeyError, TypeError):
            self.json(400, {"error": "请求字段缺失或格式不正确。"})
        except Exception:
            LOG.exception("Web write failed")
            self.json(500, {"error": "保存失败，请刷新核对后再试。"})


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server = ThreadingHTTPServer(("127.0.0.1", 8502), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
