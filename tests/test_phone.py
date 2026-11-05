import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main, serve


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main.settings, "app_token", "s3cret")
    return TestClient(main.app)


def test_page_and_pwa_files_are_public(client):
    assert client.get("/").status_code == 200
    manifest = client.get("/manifest.json").json()
    assert manifest["display"] == "standalone" and {i["sizes"] for i in manifest["icons"]} >= {"192x192", "512x512"}
    assert client.get("/sw.js").status_code == 200 and client.get("/icon-192.png").status_code == 200


def test_api_needs_token(client):
    assert client.get("/api/voices").status_code == 401
    assert client.get("/api/voices?token=wrong").status_code == 401
    assert client.get("/api/voices?token=s3cret").status_code == 200
    assert client.get("/api/voices", headers={"x-app-token": "s3cret"}).status_code == 200
    assert client.get("/reports/x").status_code == 401


def test_websocket_needs_token(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws?call=c1&token=wrong") as ws:
            ws.receive_text()
    assert e.value.code == 4401


def test_no_token_configured_means_open(monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "app_token", "")
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    assert TestClient(main.app).get("/api/voices").status_code == 200


def test_cert_command_covers_lan_ip(tmp_path):
    cmd = serve.cert_cmd("openssl", "192.168.1.20", tmp_path / "k", tmp_path / "c")
    assert cmd[cmd.index("-addext") + 1] == "subjectAltName=IP:192.168.1.20,DNS:localhost"
    assert "-nodes" in cmd and cmd[cmd.index("-days") + 1] == "30"
