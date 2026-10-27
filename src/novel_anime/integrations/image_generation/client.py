"""OpenAI-compatible image generation adapter."""

from __future__ import annotations

import base64
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from novel_anime.core.errors import AppError

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 8_000_000


class ImageGenerationClient:
    def __init__(self, profile: dict[str, Any], api_key: str) -> None:
        self.profile = dict(profile)
        self.api_key = api_key

    def generate(self, prompt: str, count: int = 1) -> list[tuple[bytes, str]]:
        """Call an OpenAI-compatible /images/generations endpoint and return validated image bytes."""
        base_url = str(self.profile["base_url"]).rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise AppError("image_invalid_config", "生图模型 API 地址无效。")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise AppError("image_invalid_config", "非本机生图服务必须使用 HTTPS。")
        try:
            response = httpx.post(
                f"{base_url}/images/generations",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.profile["model"],
                    "prompt": prompt,
                    "n": count,
                    "response_format": "b64_json",
                },
                timeout=float(self.profile.get("timeout", 300)),
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise AppError("image_timeout", "生图请求超时，服务可能已受理；请核对服务端任务后再重试。") from None
        except httpx.HTTPError:
            raise AppError("image_connection", "无法连接生图模型服务。") from None
        if response.status_code in {401, 403}:
            raise AppError("image_auth", "生图模型认证失败或没有访问权限。")
        if response.status_code == 429:
            raise AppError("image_rate_limit", "生图模型服务限流，请稍后再试。")
        if response.status_code >= 500:
            raise AppError("image_unavailable", "生图模型服务暂时不可用。")
        if response.status_code >= 400:
            raise AppError("image_request_rejected", "生图模型拒绝了请求，请检查模型 ID 和参数。")
        try:
            payload = response.json()
            rows = payload["data"]
            if not isinstance(rows, list) or not 1 <= len(rows) <= count:
                raise ValueError("unexpected result count")
            output: list[tuple[bytes, str]] = []
            for row in rows:
                encoded = row.get("b64_json")
                if not isinstance(encoded, str):
                    raise ValueError("image URL responses are not accepted")
                image = base64.b64decode(encoded, validate=True)
                if not image or len(image) > MAX_IMAGE_BYTES:
                    raise ValueError("image size")
                if image.startswith(b"\x89PNG\r\n\x1a\n"):
                    mime_type = "image/png"
                elif (
                    image.startswith(b"\xff\xd8")
                    and image.endswith(b"\xff\xd9")
                    and re.search(rb"\xff\xc[0-3]\x00", image[:512])
                ):
                    mime_type = "image/jpeg"
                else:
                    raise ValueError("image format")
                output.append((image, mime_type))
            return output
        except (KeyError, TypeError, ValueError):
            logger.warning("Image response was invalid; request_id=%s", response.headers.get("x-request-id", "unknown"))
            raise AppError("image_invalid_output", "生图服务返回的图片格式无效或超出大小限制。") from None
