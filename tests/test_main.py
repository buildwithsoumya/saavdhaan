import json
import wave

import pytest
from fastapi.testclient import TestClient

from app import alerts, gnani, main


class FakeStream:
    """Replaces SttStream: echoes scripted Gnani events when audio arrives."""
    instances = []
    script = []

    def __init__(self, on_event, keyterms=()):
        self.on_event, self.keyterms = on_event, list(keyterms)
        self.audio, self.bias_updates, self.closed = [], [], False
        FakeStream.instances.append(self)

    async def start(self):
        pass

    async def send_audio(self, pcm):
        self.audio.append(pcm)
        if FakeStream.script:
            for msg in FakeStream.script.pop(0):
                await self.on_event(msg)

    async def update_keyterms(self, terms):
        self.bias_updates.append(list(terms))

    async def close(self):
        self.closed = True


@pytest.fixture
def client(monkeypatch, tmp_path):
    FakeStream.instances, FakeStream.script = [], []
    main.CALLS.clear()
    monkeypatch.setattr(gnani, "SttStream", FakeStream)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)

    async def fake_audio(level, code, voice=None):
        return f"WAV-L{level}-{code}-{voice}".encode()

    monkeypatch.setattr(alerts, "alert_audio", fake_audio)
    return TestClient(main.app)


def said(text, lang="hi-IN"):
    return [{"type": "processing"}, {"type": "transcript", "text": text, "detected_language_code": lang}]


def receive_until(ws, kind):
    while True:
        m = ws.receive()
        if m.get("bytes") is not None:
            if kind == "audio":
                return m["bytes"]
            continue
        msg = json.loads(m["text"])
        if msg["type"] == kind:
            return msg


def test_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Saavdhaan" in r.text


def test_bridge_detects_scam_and_plays_alerts(client):
    FakeStream.script = [
        said("मैं CBI से inspector बोल रहा हूँ"),
        said("आपके नाम पर arrest warrant है"),
        said("तुरंत safe account में transfer कीजिए"),
    ]
    with client.websocket_connect("/ws?call=c1&voice=Avinash") as ws:
        assert receive_until(ws, "ready")["explain"]["level"] == 0
        ws.send_bytes(b"\0" * 1024)
        assert receive_until(ws, "speech_end") == {"type": "speech_end"}
        assert receive_until(ws, "transcript")["explain"]["level"] == 0
        ws.send_bytes(b"\0" * 1024)
        t = receive_until(ws, "transcript")
        assert t["explain"]["level"] == 1 and t["lang"] == "hi-IN"
        assert receive_until(ws, "alert")["level"] == 1
        assert receive_until(ws, "audio") == b"WAV-L1-hi-IN-Avinash"
        ws.send_bytes(b"\0" * 1024)
        assert receive_until(ws, "transcript")["explain"]["level"] == 3
        alert = receive_until(ws, "alert")
        assert alert["level"] == 3 and "उन्नीस सौ तीस" in alert["text"]
        assert receive_until(ws, "audio") == b"WAV-L3-hi-IN-Avinash"
        ws.send_text(json.dumps({"type": "stop"}))
    stream = FakeStream.instances[0]
    assert stream.closed and len(stream.audio) == 3
    assert stream.bias_updates and "OTP" in stream.bias_updates[0]  # stage-following after S1+S2


def test_state_survives_browser_reconnect(client):
    FakeStream.script = [said("CBI officer"), said("arrest warrant hai")]
    with client.websocket_connect("/ws?call=c2") as ws:
        receive_until(ws, "ready")
        ws.send_bytes(b"\0" * 1024)
        receive_until(ws, "transcript")
    with client.websocket_connect("/ws?call=c2") as ws:
        assert receive_until(ws, "ready")["explain"]["stages"].keys() == {"S1"}
        ws.send_bytes(b"\0" * 1024)
        assert receive_until(ws, "transcript")["explain"]["level"] == 1
    assert FakeStream.instances[1].bias_updates  # S1 from the first connection + S2 now
    with client.websocket_connect("/ws?call=c2") as ws:
        receive_until(ws, "ready")
    assert "OTP" in FakeStream.instances[2].keyterms  # a new session starts already following


def test_alert_not_repeated_for_same_level(client):
    FakeStream.script = [said("CBI"), said("arrest"), said("police arrest again")]
    with client.websocket_connect("/ws?call=c3") as ws:
        receive_until(ws, "ready")
        for _ in range(3):
            ws.send_bytes(b"\0" * 1024)
            receive_until(ws, "transcript")
        ws.send_text(json.dumps({"type": "stop"}))
    assert main.CALLS["c3"].alerted == 1


def test_opted_in_call_is_recorded(client, tmp_path):
    with client.websocket_connect("/ws?call=c4&record=1") as ws:
        receive_until(ws, "ready")
        ws.send_bytes(b"\1\0" * 16000)
        ws.send_text(json.dumps({"type": "stop"}))
    with wave.open(str(tmp_path / "calls" / "c4.wav")) as w:
        assert w.getframerate() == 16000 and w.getnframes() == 16000


def test_alert_text_every_language_mentions_helpline():
    for lang, m in alerts.MESSAGES.items():
        for level in (1, 2, 3):
            assert "{helpline}" in m[f"L{level}"], (lang, level)
            assert m["helpline"] in alerts.alert_text(level, f"{lang}-IN")
        assert lang in ("hi", "en") or m.get("needs_native_review") is True
    assert alerts.alert_text(2, "gu-IN") == alerts.alert_text(2, "hi-IN")  # fallback


def test_alert_audio_is_tagged_and_cached(monkeypatch, tmp_path):
    import asyncio
    monkeypatch.setattr(alerts.settings, "data_dir", tmp_path)
    calls = []

    async def fake_tts(text, voice, language):
        calls.append((text, voice, language))
        return b"RIFF"

    monkeypatch.setattr(gnani, "tts", fake_tts)
    for _ in range(2):
        assert asyncio.run(alerts.alert_audio(3, "ta-IN", "Shivani")) == b"RIFF"
    assert len(calls) == 1
    text, voice, language = calls[0]
    assert text.startswith("<shouting>") and "<pauses>" in text and language == "ta-IN"


def test_call_id_must_be_safe(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws?call=../../evil&record=1") as ws:
            ws.receive_text()
    assert e.value.code == 1008 and not FakeStream.instances


def test_failed_alert_is_retried_on_next_transcript(client, monkeypatch):
    attempts = []

    async def flaky_audio(level, code, voice=None):
        attempts.append(level)
        if len(attempts) == 1:
            raise TimeoutError("tts timed out")
        return b"WAV"

    monkeypatch.setattr(alerts, "alert_audio", flaky_audio)
    FakeStream.script = [said("CBI officer"), said("arrest warrant hai"), said("police case")]
    with client.websocket_connect("/ws?call=r1") as ws:
        receive_until(ws, "ready")
        for _ in range(2):
            ws.send_bytes(b"\0" * 1024)
            receive_until(ws, "transcript")
        assert "alert audio failed" in receive_until(ws, "error")["message"]
        ws.send_bytes(b"\0" * 1024)
        assert receive_until(ws, "alert")["level"] == 1
        ws.send_text("not json")  # ignored
        ws.send_text(json.dumps({"type": "stop"}))
    assert attempts == [1, 1]


def test_reconnect_replaces_the_previous_socket(client):
    with client.websocket_connect("/ws?call=dup") as first:
        receive_until(first, "ready")
        with client.websocket_connect("/ws?call=dup") as second:
            receive_until(second, "ready")
            assert first.receive()["type"] == "websocket.close"  # old browser socket closed
            assert main.CALLS["dup"].socket is not None


def test_apk_is_served_from_the_release_and_cached(client, monkeypatch):
    fetched = []

    async def fake_fetch():
        fetched.append(1)
        return b"APK-BYTES"

    monkeypatch.setattr(main, "fetch_apk", fake_fetch)
    for _ in range(2):
        r = client.get("/saavdhaan.apk")
        assert r.status_code == 200 and r.content == b"APK-BYTES"
        assert r.headers["content-type"] == "application/vnd.android.package-archive"
    assert len(fetched) == 1  # the second download comes from the cache


def test_apk_unavailable_without_cache_is_a_clear_503(client, monkeypatch):
    async def broken():
        raise RuntimeError("no network")

    monkeypatch.setattr(main, "fetch_apk", broken)
    r = client.get("/saavdhaan.apk")
    assert r.status_code == 503 and "not available" in r.text
    assert r.headers["content-type"].startswith("text/html")  # never saved as a file
