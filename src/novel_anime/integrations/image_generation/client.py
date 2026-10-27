"""OpenAI-compatible image generation adapter."""

from __future__ import annotations

import base64
import logging
import re
import subprocess
import tempfile
from pathlib import Path
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

    def generate(
        self, prompt: str, count: int = 1, references: list[tuple[bytes, str]] | None = None
    ) -> list[tuple[bytes, str]]:
        """Generate images, forwarding optional references to the backend's editing interface."""
        if self.profile.get("backend") == "mflux":
            return self._generate_mflux(prompt, count, references)
        base_url = str(self.profile["base_url"]).rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise AppError("image_invalid_config", "生图模型 API 地址无效。")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise AppError("image_invalid_config", "非本机生图服务必须使用 HTTPS。")
        try:
            if references:
                response = httpx.post(
                    f"{base_url}/images/edits",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data={"model": self.profile["model"], "prompt": prompt, "n": str(count)},
                    files=[
                        (
                            "image[]",
                            (
                                f"reference-{index}." + ("png" if mime == "image/png" else "jpg"),
                                content,
                                mime,
                            ),
                        )
                        for index, (content, mime) in enumerate(references)
                    ],
                    timeout=float(self.profile.get("timeout", 300)),
                    follow_redirects=False,
                )
            else:
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
            raise AppError(
                "image_timeout", "生图请求超时，服务可能已受理；请核对服务端任务后再重试。"
            ) from None
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
            logger.warning(
                "Image response was invalid; request_id=%s",
                response.headers.get("x-request-id", "unknown"),
            )
            raise AppError(
                "image_invalid_output", "生图服务返回的图片格式无效或超出大小限制。"
            ) from None

    def _generate_mflux(
        self, prompt: str, count: int, references: list[tuple[bytes, str]] | None = None
    ) -> list[tuple[bytes, str]]:
        executable = Path(str(self.profile.get("executable", ""))).expanduser()
        if references:
            executable = executable.with_name("mflux-generate-flux2-edit")
        if not executable.is_file():
            raise AppError(
                "image_invalid_config", "找不到 mflux-generate-flux2，请检查本地 FLUX.2 配置。"
            )
        model = str(self.profile.get("model", "flux2-klein-4b"))
        base_model = str(self.profile.get("base_model", "flux2-klein-4b"))
        try:
            with tempfile.TemporaryDirectory(prefix="yingzhang-flux2-") as temp_dir:
                reference_paths = []
                for index, (content, mime) in enumerate(references or []):
                    reference = Path(temp_dir) / (
                        f"reference-{index}." + ("png" if mime == "image/png" else "jpg")
                    )
                    reference.write_bytes(content)
                    reference_paths.append(str(reference))
                outputs = []
                for index in range(count):
                    path = Path(temp_dir) / f"image-{index}.png"
                    command = [
                        str(executable),
                        "--model",
                        model,
                        "--base-model",
                        base_model,
                        "--prompt",
                        prompt,
                        "--width",
                        "1024",
                        "--height",
                        "1024",
                        "--steps",
                        "4",
                        "--low-ram",
                        "--output",
                        str(path),
                    ]
                    if reference_paths:
                        command.extend(["--image-paths", *reference_paths])
                    try:
                        subprocess.run(
                            command,
                            check=True,
                            capture_output=True,
                            text=True,
                            timeout=float(self.profile.get("timeout", 300)),
                        )
                    except subprocess.TimeoutExpired:
                        raise AppError("image_timeout", "FLUX.2 本地生图超时。") from None
                    except subprocess.CalledProcessError:
                        logger.exception("Local FLUX.2 generation failed")
                        raise AppError(
                            "image_unavailable", "FLUX.2 本地生图失败，请检查 mflux 环境。"
                        ) from None
                    image = path.read_bytes()
                    if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > MAX_IMAGE_BYTES:
                        raise ValueError("invalid output")
                    outputs.append((image, "image/png"))
                return outputs
        except OSError:
            logger.exception("Local FLUX.2 output could not be read")
            raise AppError("image_invalid_output", "FLUX.2 未生成有效 PNG 图片。") from None
