import base64
import json
from pathlib import Path

import httpx
import pytest

from novel_anime.core.errors import AppError
from novel_anime.integrations.image_generation.client import ImageGenerationClient

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"


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
        "size": "1024x1024",
    }
    assert captured["follow_redirects"] is False


def test_mflux_backend_runs_local_generator_and_returns_png(tmp_path, monkeypatch):
    executable = tmp_path / "mflux-generate-flux2"
    executable.touch()
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[-1]).write_bytes(PNG)

    monkeypatch.setattr("subprocess.run", run)
    result = ImageGenerationClient(
        {
            "backend": "mflux",
            "executable": str(executable),
            "model": "flux-q8",
            "base_model": "flux2-klein-4b",
            "timeout": 900,
        },
        "",
    ).generate("A character", 2)
    assert result == [(PNG, "image/png"), (PNG, "image/png")]
    assert len(calls) == 2
    assert "--low-ram" in calls[0][0]
    assert calls[0][1]["timeout"] == 900


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "image_auth"),
        (403, "image_auth"),
        (429, "image_rate_limit"),
        (503, "image_unavailable"),
        (400, "image_request_rejected"),
    ],
)
def test_image_api_errors_are_classified(monkeypatch, status, code):
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: httpx.Response(status))
    client = ImageGenerationClient(
        {"base_url": "https://images.example/v1", "model": "illustrator"}, "key"
    )
    with pytest.raises(AppError) as caught:
        client.generate("A character")
    assert caught.value.code == code


def test_reference_images_are_sent_as_multipart_edits(monkeypatch):
    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    monkeypatch.setattr(httpx, "post", post)
    ImageGenerationClient(
        {"base_url": "https://images.example/v1", "model": "test"}, "key"
    ).generate("Use references", references=[(PNG, "image/png")])
    assert captured["url"].endswith("/images/edits")
    assert captured["files"][0][1][1] == PNG
    assert captured["data"]["prompt"] == "Use references"


def test_mflux_references_use_edit_executable_and_real_files(tmp_path, monkeypatch):
    executable = tmp_path / "mflux-generate-flux2"
    executable.touch()
    executable.with_name("mflux-generate-flux2-edit").touch()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        reference_paths = command[command.index("--image-paths") + 1 :]
        assert [Path(path).read_bytes() for path in reference_paths] == [PNG, PNG]
        Path(command[command.index("--output") + 1]).write_bytes(PNG)

    monkeypatch.setattr("subprocess.run", run)
    client = ImageGenerationClient({"backend": "mflux", "executable": str(executable)}, "")
    assert client.generate("Use both references", references=[(PNG, "image/png")] * 2) == [
        (PNG, "image/png")
    ]
    assert calls[0][0].endswith("mflux-generate-flux2-edit")


def test_selected_image_dimensions_reach_api(monkeypatch):
    captured = {}
    def post(url, **kwargs):
        captured.update(kwargs)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
    monkeypatch.setattr(httpx, "post", post)
    client = ImageGenerationClient({"base_url": "https://images.example/v1", "model": "test",
                                    "width": 864, "height": 1536}, "key")
    client.generate("portrait")
    assert captured["json"]["size"] == "864x1536"
    client.generate("portrait", references=[(PNG, "image/png")])
    assert captured["data"]["size"] == "864x1536"


def test_image_model_catalog(monkeypatch):
    def get(url, **kwargs):
        assert url == 'https://images.example/v1/models'
        assert kwargs['headers']['Authorization'] == 'Bearer private-key'
        assert kwargs['follow_redirects'] is False
        return httpx.Response(200, request=httpx.Request('GET', url), json={
            'data': [{'id': 'flux'}, {'id': 'gpt-image'}, {'id': 'flux'}, {'id': None}]
        })
    monkeypatch.setattr(httpx, 'get', get)
    client = ImageGenerationClient({'base_url': 'https://images.example/v1'}, 'private-key')
    assert client.list_models() == ['flux', 'gpt-image']


def test_image_model_catalog_rejects_remote_http():
    with pytest.raises(AppError):
        ImageGenerationClient({'base_url': 'http://images.example/v1'}, '').list_models()
