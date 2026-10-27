import base64
import json

import httpx
import pytest

from novel_anime.core.errors import AppError
from novel_anime.integrations.image_generation.client import ImageGenerationClient


PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00"
)


def test_openai_compatible_image_request(monkeypatch):
    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(PNG).decode("ascii")}]},
        )

    monkeypatch.setattr(httpx, "post", post)
    result = ImageGenerationClient(
        {"base_url": "https://images.example/v1", "model": "illustrator", "timeout": 60},
        "private-key",
    ).generate("A character", 1)
    assert result == [(PNG, "image/png")]
    assert captured["url"] == "https://images.example/v1/images/generations"
    assert captured["headers"]["Authorization"] == "Bearer private-key"
    assert json.loads(json.dumps(captured["json"])) == {
        "model": "illustrator",
        "prompt": "A character",
        "n": 1,
        "response_format": "b64_json",
    }
    assert captured["follow_redirects"] is False


@pytest.mark.parametrize("status,code", [(401, "image_auth"), (403, "image_auth"), (429, "image_rate_limit"), (503, "image_unavailable"), (400, "image_request_rejected")])
def test_image_api_errors_are_classified(monkeypatch, status, code):
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: httpx.Response(status))
    client = ImageGenerationClient(
        {"base_url": "https://images.example/v1", "model": "illustrator"}, "key"
    )
    with pytest.raises(AppError) as caught:
        client.generate("A character")
    assert caught.value.code == code
