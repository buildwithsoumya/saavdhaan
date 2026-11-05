import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import alerts, gnani, honeypot, main
from app.tracker import Tracker
from tests.test_main import FakeStream, receive_until, said


def test_safe_blocks_numbers_payment_claims_and_tags():
    assert honeypot.safe("Haan beta, OTP hai 4 8 2 1", "hi-IN") == honeypot.STALL["hi"]
    assert honeypot.safe("मेरा नंबर ९८७ है", "hi-IN") == honeypot.STALL["hi"]
    assert honeypot.safe("Okay I have transferred it", "en-IN") == honeypot.STALL["en"]
    assert honeypot.safe("मैंने पैसे भेज दिए", "hi-IN") == honeypot.STALL["hi"]
    assert honeypot.safe("<whispers> (nervous) Which bank did you say, sir?", "en-IN") == "Which bank did you say, sir?"
    assert honeypot.safe("", "ta-IN") == honeypot.STALL["ta"]
    assert honeypot.safe("x" * 300, "gu-IN") == honeypot.STALL["hi"]  # unknown language falls back


def test_evidence_needs_authority_threat_money_and_turns():
    tr = Tracker()
    tr.feed("CBI officer, arrest warrant hai")
    assert not honeypot.evidence_complete(tr, 10)
    tr.feed("UPI se transfer karo")
    assert not honeypot.evidence_complete(tr, honeypot.MIN_EXCHANGES - 1)
    assert honeypot.evidence_complete(tr, honeypot.MIN_EXCHANGES)


def test_groq_request_and_failure_fallback(monkeypatch):
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Aapka naam kya hai sahab?"}}]})

    monkeypatch.setattr(honeypot.settings, "groq_api_key", "gk")
    monkeypatch.setattr(honeypot, "transport", httpx.MockTransport(handler))
    history = [{"role": "user", "content": "OTP batao"}]
    assert asyncio.run(honeypot.reply(history, "hi-IN")) == "Aapka naam kya hai sahab?"
    assert seen["auth"] == "Bearer gk" and seen["body"]["model"] == honeypot.settings.groq_model
    system = seen["body"]["messages"][0]
    assert system["role"] == "system" and "Hindi" in system["content"] and "Never say any number" in system["content"]
    assert seen["body"]["messages"][1:] == history

    monkeypatch.setattr(honeypot, "transport", httpx.MockTransport(lambda r: httpx.Response(429)))
    assert asyncio.run(honeypot.reply(history, "bn-IN")) == honeypot.STALL["bn"]


@pytest.fixture
def client(monkeypatch):
    FakeStream.instances, FakeStream.script = [], []
    main.CALLS.clear()
    monkeypatch.setattr(gnani, "SttStream", FakeStream)
    monkeypatch.setattr(main.settings, "groq_api_key", "gk")
    alert_calls, tts_calls = [], []

    async def fake_alert(level, code, voice=None):
        alert_calls.append(level)
        return b"ALERT"

    async def fake_tts(text, voice, language, speed=1.0):
        tts_calls.append((text, voice, language))
        return b"PERSONA"

    async def fake_reply(history, code):
        return f"reply {len(history)}"

    monkeypatch.setattr(alerts, "alert_audio", fake_alert)
    monkeypatch.setattr(gnani, "tts", fake_tts)
    monkeypatch.setattr(honeypot, "reply", fake_reply)
    c = TestClient(main.app)
    c.alert_calls, c.tts_calls = alert_calls, tts_calls
    return c


def test_honeypot_flow(client, tmp_path):
    FakeStream.script = [said("CBI se inspector bol raha hoon"), said("arrest warrant hai, kisi ko mat batana"),
                         said("kaun bol raha hai?"), said("UPI se transfer karo"), said("jaldi karo"),
                         said("abhi bhejo")]
    with client.websocket_connect("/ws?call=hp1") as ws:
        receive_until(ws, "ready")
        ws.send_text(json.dumps({"type": "honeypot", "on": True}))
        assert "L2" in receive_until(ws, "error")["message"]  # not allowed below L2
        for _ in range(2):
            ws.send_bytes(b"\0" * 1024)
            receive_until(ws, "transcript")
        receive_until(ws, "audio")  # the L2 alert, before honeypot
        ws.send_text(json.dumps({"type": "honeypot", "on": True}))
        assert receive_until(ws, "honeypot") == {"type": "honeypot", "on": True, "reason": "user"}
        replies = []
        for _ in range(4):
            ws.send_bytes(b"\0" * 1024)
            replies.append(receive_until(ws, "say")["text"])
            assert receive_until(ws, "audio") == b"PERSONA"
        assert replies == ["reply 1", "reply 3", "reply 5", "reply 7"]
        done = receive_until(ws, "honeypot")
        assert done == {"type": "honeypot", "on": False, "reason": "evidence"}
        ws.send_text(json.dumps({"type": "stop"}))
    assert client.alert_calls == [2]  # the L3 alert was held back while the persona talked
    assert {v for _, v, _ in client.tts_calls} == {main.settings.honeypot_voice}
    log = json.loads((tmp_path / "honeypot" / "hp1.json").read_text(encoding="utf-8"))
    assert [m["role"] for m in log] == ["user", "assistant"] * 4


def test_honeypot_needs_groq_key(client, monkeypatch):
    monkeypatch.setattr(main.settings, "groq_api_key", "")
    with client.websocket_connect("/ws?call=hp2") as ws:
        receive_until(ws, "ready")
        ws.send_text(json.dumps({"type": "honeypot", "on": True}))
        assert "GROQ_API_KEY" in receive_until(ws, "error")["message"]
