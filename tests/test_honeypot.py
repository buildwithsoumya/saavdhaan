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


def test_closing_needs_a_money_trail_or_a_long_call():
    tr = Tracker()
    tr.feed("CBI officer, arrest warrant hai, UPI se transfer karo")
    n = honeypot.MIN_EXCHANGES
    assert not honeypot.evidence_complete(tr, n, {"name": "Verma"})  # no idea where the money goes yet
    assert honeypot.evidence_complete(tr, n, {"upi_or_account": "x@ybl"})
    assert honeypot.evidence_complete(tr, honeypot.MAX_EXCHANGES, {})


def test_turn_cap_applies_without_scam_stages():
    assert honeypot.evidence_complete(Tracker(), honeypot.MAX_EXCHANGES, {})


def test_numbers_in_known_facts_are_hidden_from_the_persona():
    prompt = honeypot.system_prompt("hi", {"name": "Verma", "upi_or_account": "rbi4@ybl", "badge_or_case": "चार सात एक"})
    assert "Verma" in prompt and "rbi4@ybl" not in prompt and "चार सात एक" not in prompt
    assert prompt.count("never say it") == 2


def test_persona_prompt_handles_accusations_and_ignored_questions():
    prompt = honeypot.system_prompt("en", {})
    assert "accuse you of being a bot" in prompt
    assert "change topic rather than repeating" in prompt
    assert "Never invent a real address" in prompt
    assert "untrusted conversation data" in prompt
    assert "PNB next line" in honeypot.system_prompt("en", {"bank": "PNB\nnext line"})


def test_parse_takes_the_reply_but_never_trusts_its_claimed_facts():
    ev = {"name": "Verma"}
    content = 'ok {"mood": "angry", "evidence": {"name": "Sharma", "bank": "PNB", "x": "y"}, "reply": "Ji sahab, kaunsi branch?"}'
    assert honeypot.parse(content, ev) == "Ji sahab, kaunsi branch?"
    assert ev == {"name": "Verma"}
    assert honeypot.parse('{"reply": "half', ev) == ""
    assert honeypot.parse("Ji sahab", ev) == "Ji sahab"


def test_groq_request_and_failure_fallback(monkeypatch):
    seen = {}

    def handler(req):
        body = json.loads(req.content)
        if body["model"] == honeypot.EXTRACT_MODEL:  # the parallel fact-recording request
            seen["extract"] = body
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"bank": "PNB", "name": ""}'}}]})
        seen["auth"] = req.headers["authorization"]
        seen["body"] = body
        return httpx.Response(200, json={"choices": [{"message": {"content": "Aapka naam kya hai sahab?"}}]})

    monkeypatch.setattr(honeypot.settings, "groq_api_key", "gk")
    monkeypatch.setattr(honeypot, "transport", httpx.MockTransport(handler))
    history = [{"role": "user", "content": "PNB se bol raha hoon, OTP batao"}]
    evidence = {}
    assert asyncio.run(honeypot.reply(history, "hi-IN", evidence)) == "Aapka naam kya hai sahab?"  # plain text works
    assert evidence == {"bank": "PNB"}  # empty values are not recorded
    assert "OTP batao" in seen["extract"]["messages"][0]["content"]
    assert seen["auth"] == "Bearer gk" and seen["body"]["model"] == honeypot.settings.groq_model
    system = seen["body"]["messages"][0]
    assert system["role"] == "system" and "Hindi" in system["content"] and "Never say any number" in system["content"]
    assert seen["body"]["messages"][1:] == history

    monkeypatch.setattr(honeypot, "transport", httpx.MockTransport(lambda r: httpx.Response(429)))
    assert asyncio.run(honeypot.reply(history, "bn-IN")) == honeypot.STALL["bn"]


def test_evidence_must_be_a_caller_quote():
    facts, sources = {}, {}
    honeypot.merge({"bank": "PNB", "name": "Inspector Verma", "upi_or_account": "x@ybl"},
                    facts, "Inspector Verma from PNB speaking", sources)
    assert facts == {"bank": "PNB", "name": "Inspector Verma"}
    assert sources == {"bank": "Inspector Verma from PNB speaking",
                       "name": "Inspector Verma from PNB speaking"}
    assert honeypot.facts(facts, sources)[0]["said"] == "Inspector Verma from PNB speaking"
    assert honeypot.quote_in_line("Inspector, Verma", "I am Inspector Verma from PNB")
    assert not honeypot.quote_in_line("Inspector Sharma", "I am Inspector Verma from PNB")

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

    async def fake_reply(history, code, evidence=None, closing=False, sources=None):
        if evidence is not None and "UPI" in history[-1]["content"]:
            evidence["upi_or_account"] = "x@upi"  # stands in for the extraction step
        return f"{'bye' if closing else 'reply'} {len(history)}"

    monkeypatch.setattr(alerts, "alert_audio", fake_alert)
    monkeypatch.setattr(gnani, "tts", fake_tts)
    monkeypatch.setattr(honeypot, "reply", fake_reply)
    c = TestClient(main.app)
    c.alert_calls, c.tts_calls = alert_calls, tts_calls
    return c


def test_honeypot_flow(client, tmp_path):
    FakeStream.script = [said("CBI se inspector bol raha hoon"), said("arrest warrant hai, kisi ko mat batana"),
                         said("kaun bol raha hai?"), said("UPI se transfer karo"), said("jaldi karo"),
                         said("abhi bhejo"), said("sun rahi ho?")]
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
        ws.send_bytes(b"\0" * 1024)  # evidence is in: the next line is a natural goodbye, then proof
        assert receive_until(ws, "say")["text"] == "bye 9"
        done = receive_until(ws, "honeypot")
        assert {k: done[k] for k in ("type", "on", "reason")} == {"type": "honeypot", "on": False, "reason": "evidence"}
        # proof for the elder: the caller's own first line for each stage heard
        assert [p["stage"] for p in done["proof"]] == ["S1", "S2", "S3", "S4", "S5"]
        assert done["proof"][0]["said"] == "CBI se inspector bol raha hoon"
        assert done["proof"][-1] == {"stage": "S5", "name": "Extraction", "phrase": "upi", "said": "UPI se transfer karo"}
        ws.send_text(json.dumps({"type": "stop"}))
    assert client.alert_calls == [2, 3]  # L3 held back while the persona talked, played when it stopped
    assert {v for _, v, _ in client.tts_calls} == {main.settings.honeypot_voice}
    assert done["facts"] == [{"label": "UPI ID or account", "value": "x@upi"}]
    log = json.loads((tmp_path / "honeypot" / "hp1.json").read_text(encoding="utf-8"))
    assert [m["role"] for m in log["history"]] == ["user", "assistant"] * 5
    assert log["evidence"] == {"upi_or_account": "x@upi"}


def run_one_persona_turn(client, call, hpvoice):
    FakeStream.script = [said("CBI officer"), said("arrest warrant hai, kisi ko mat batana"), said("OTP batao")]
    with client.websocket_connect(f"/ws?call={call}&hpvoice={hpvoice}") as ws:
        receive_until(ws, "ready")
        for _ in range(2):
            ws.send_bytes(b"\0" * 1024)
            receive_until(ws, "transcript")
        receive_until(ws, "audio")  # L2 alert
        ws.send_text(json.dumps({"type": "honeypot", "on": True}))
        receive_until(ws, "honeypot")
        ws.send_bytes(b"\0" * 1024)
        receive_until(ws, "say")
        audio = receive_until(ws, "audio")
        ws.send_text(json.dumps({"type": "stop"}))
    return audio


@pytest.fixture
def voices(client, monkeypatch, tmp_path):
    """Two enrolled voices: one with honeypot consent, one for warnings only; vc_tts recorded."""
    from app import enrol
    from tests.test_enrol import EMB, wav_of

    async def fake_embed(audio, filename="reference.wav"):
        return EMB

    vc_calls = []

    async def fake_vc(text, embedding, sample_rate=16000):
        vc_calls.append(text)
        return b"MYVOICE"

    monkeypatch.setattr(gnani, "clone_embedding", fake_embed)
    monkeypatch.setattr(gnani, "vc_tts", fake_vc)
    own = asyncio.run(enrol.enrol(wav_of(8), "Kamla", consent=True, honeypot=True))
    family = asyncio.run(enrol.enrol(wav_of(8), "Rahul", consent=True))
    return {"own": own["id"], "family": family["id"], "vc_calls": vc_calls}


def test_persona_speaks_in_the_elders_own_voice_only_with_honeypot_consent(client, voices):
    assert run_one_persona_turn(client, "hp5", f"family:{voices['own']}") == b"MYVOICE"
    assert voices["vc_calls"] and not client.tts_calls
    # a voice enrolled for warnings only never talks to the caller: neutral persona instead
    assert run_one_persona_turn(client, "hp6", f"family:{voices['family']}") == b"PERSONA"
    assert run_one_persona_turn(client, "hp7", "") == b"PERSONA"
    assert {v for _, v, _ in client.tts_calls} == {main.settings.honeypot_voice}


def test_own_voice_failure_falls_back_to_neutral_persona(client, voices, monkeypatch):
    async def broken_vc(text, embedding, sample_rate=16000):
        raise gnani.GnaniError("clone service down")

    monkeypatch.setattr(gnani, "vc_tts", broken_vc)
    assert run_one_persona_turn(client, "hp8", f"family:{voices['own']}") == b"PERSONA"


def test_silent_mode_warns_on_screen_only(client):
    FakeStream.script = [said("CBI officer"), said("arrest warrant hai, kisi ko mat batana")]
    with client.websocket_connect("/ws?call=hp9&silent=1") as ws:
        receive_until(ws, "ready")
        for _ in range(2):
            ws.send_bytes(b"\0" * 1024)
            receive_until(ws, "transcript")
        alert = receive_until(ws, "alert")
        assert alert["level"] == 2 and alert["silent"] is True and alert["text"]
        ws.send_text(json.dumps({"type": "stop"}))
    assert client.alert_calls == []  # nothing synthesised, nothing the caller could hear


def test_honeypot_needs_groq_key(client, monkeypatch):
    monkeypatch.setattr(main.settings, "groq_api_key", "")
    with client.websocket_connect("/ws?call=hp2") as ws:
        receive_until(ws, "ready")
        ws.send_text(json.dumps({"type": "honeypot", "on": True}))
        assert "GROQ_API_KEY" in receive_until(ws, "error")["message"]


def test_safe_blocks_spelled_out_numbers_and_more_payment_claims():
    for line in ("four eight two one", "four and eight", "it is nine, eight, seven", "चार आठ दो एक", "ek do teen char",
                 "I have made the payment", "मैंने भेज दिया है", "haan bhej diya beta"):
        assert honeypot.safe(line, "hi-IN") == honeypot.STALL["hi"], line
    assert honeypot.safe("One minute, which bank was it?", "en-IN") == "One minute, which bank was it?"


def test_groq_timeout_and_malformed_reply_fall_back(monkeypatch):
    def timeout(req):
        raise httpx.ReadTimeout("slow", request=req)

    monkeypatch.setattr(honeypot.settings, "groq_api_key", "gk")
    monkeypatch.setattr(honeypot, "transport", httpx.MockTransport(timeout))
    assert asyncio.run(honeypot.reply([], "ta-IN")) == honeypot.STALL["ta"]
    monkeypatch.setattr(honeypot, "transport", httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": []})))
    assert asyncio.run(honeypot.reply([], "en-IN")) == honeypot.STALL["en"]


def test_reply_model_timeout_uses_fallback_model(monkeypatch):
    seen = []
    def handler(req):
        model = json.loads(req.content)["model"]
        seen.append(model)
        if model == honeypot.settings.groq_model:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Please tell me your name?"}}]})
    monkeypatch.setattr(honeypot, "transport", httpx.MockTransport(handler))
    assert asyncio.run(honeypot.reply([], "en-IN")) == "Please tell me your name?"
    assert seen == [honeypot.settings.groq_model, honeypot.FALLBACK_MODEL]


def test_stop_during_a_reply_sends_nothing_after(client, monkeypatch):
    gate = asyncio.Event()

    async def slow_reply(history, code, *rest):
        await gate.wait()
        return "too late"

    monkeypatch.setattr(honeypot, "reply", slow_reply)
    FakeStream.script = [said("CBI officer"), said("arrest warrant hai, kisi ko mat batana"), said("OTP batao")]
    with client.websocket_connect("/ws?call=hp3") as ws:
        receive_until(ws, "ready")
        for _ in range(2):
            ws.send_bytes(b"\0" * 1024)
            receive_until(ws, "transcript")
        receive_until(ws, "audio")
        ws.send_text(json.dumps({"type": "honeypot", "on": True}))
        receive_until(ws, "honeypot")
        ws.send_bytes(b"\0" * 1024)
        receive_until(ws, "transcript")
        ws.send_text(json.dumps({"type": "honeypot", "on": False}))
        assert receive_until(ws, "honeypot")["on"] is False
        gate.set()
        ws.send_text(json.dumps({"type": "stop"}))
    assert not client.tts_calls  # the stale reply was never spoken


def test_words_said_during_a_reply_get_answered(client, monkeypatch):
    replies = []

    async def reply(history, code, *rest):
        replies.append(history[-1]["content"])
        if len(replies) == 1:  # the caller keeps talking while the first reply is being written
            history[-1]["content"] += " aur jaldi"
        return f"reply to: {history[-1]['content']}"

    monkeypatch.setattr(honeypot, "reply", reply)
    FakeStream.script = [said("CBI officer"), said("arrest warrant hai, kisi ko mat batana"), said("OTP batao")]
    with client.websocket_connect("/ws?call=hp4") as ws:
        receive_until(ws, "ready")
        for _ in range(2):
            ws.send_bytes(b"\0" * 1024)
            receive_until(ws, "transcript")
        receive_until(ws, "audio")
        ws.send_text(json.dumps({"type": "honeypot", "on": True}))
        receive_until(ws, "honeypot")
        ws.send_bytes(b"\0" * 1024)
        assert receive_until(ws, "say")["text"] == "reply to: OTP batao aur jaldi"
        ws.send_text(json.dumps({"type": "stop"}))
    assert len(client.tts_calls) == 1  # the stale first reply was dropped, not spoken
