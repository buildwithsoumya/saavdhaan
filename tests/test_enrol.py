import asyncio
import io
import json
import wave

import pytest
from fastapi.testclient import TestClient

from app import alerts, enrol, gnani, main

EMB = {"embedding": "abc", "shape": [1, 768], "dtype": "torch.bfloat16"}


def wav_of(seconds):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * seconds))
    return buf.getvalue()


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setattr(enrol.settings, "data_dir", tmp_path)
    calls = []

    async def fake_embed(audio, filename="reference.wav"):
        calls.append(audio)
        return EMB

    monkeypatch.setattr(gnani, "clone_embedding", fake_embed)
    return calls


def test_enrol_requires_consent_and_valid_length(data):
    with pytest.raises(enrol.EnrolError, match="consent"):
        asyncio.run(enrol.enrol(wav_of(10), "Rahul", consent=False))
    with pytest.raises(enrol.EnrolError, match="5-30 seconds"):
        asyncio.run(enrol.enrol(wav_of(2), "Rahul", consent=True))
    with pytest.raises(enrol.EnrolError, match="read the audio"):
        asyncio.run(enrol.enrol(b"not audio", "Rahul", consent=True))
    assert data == []  # nothing sent to the API


def test_enrol_caches_embedding_with_consent_then_delete(data, tmp_path):
    v = asyncio.run(enrol.enrol(wav_of(8), " Rahul (beta) ", consent=True))
    assert v["name"] == "Rahul (beta)"
    saved = json.loads((tmp_path / "embeddings" / f"{v['id']}.json").read_text(encoding="utf-8"))
    assert saved["consent"] is True and saved["embedding"] == EMB
    assert enrol.voices() == [v] and enrol.load(v["id"])["embedding"] == EMB
    assert enrol.load("../../etc") is None
    assert enrol.delete(v["id"]) and enrol.voices() == [] and enrol.load(v["id"]) is None


def test_api_enrol_list_delete(data):
    c = TestClient(main.app)
    r = c.post("/api/voices", data={"name": "Rahul", "consent": "false"}, files={"audio": ("a.wav", wav_of(8))})
    assert r.status_code == 400
    r = c.post("/api/voices", data={"name": "Rahul", "consent": "true"}, files={"audio": ("a.wav", wav_of(8))})
    assert r.status_code == 200
    vid = r.json()["id"]
    assert c.get("/api/voices").json() == [{"id": vid, "name": "Rahul", "honeypot": False}]
    assert c.delete(f"/api/voices/{vid}").status_code == 200
    assert c.delete(f"/api/voices/{vid}").status_code == 404


def test_family_voice_used_for_l2_untagged_with_fallbacks(data, monkeypatch):
    v = asyncio.run(enrol.enrol(wav_of(8), "Rahul", consent=True))
    vc_calls, tts_calls = [], []
    fail = {"vc": False}

    async def fake_vc(text, embedding):
        vc_calls.append(text)
        if fail["vc"]:
            raise gnani.GnaniError("vc down")
        return b"VC"

    async def fake_tts(text, voice, language):
        tts_calls.append((text, voice))
        return b"TIMBRE"

    monkeypatch.setattr(gnani, "vc_tts", fake_vc)
    monkeypatch.setattr(gnani, "tts", fake_tts)
    fam = f"family:{v['id']}"

    assert asyncio.run(alerts.alert_audio(2, "hi-IN", fam)) == b"VC"
    assert "<" not in vc_calls[0] and "मैं हूँ" in vc_calls[0]
    # tagged levels stay on Timbre v3.0 with the default voice
    assert asyncio.run(alerts.alert_audio(3, "hi-IN", fam)) == b"TIMBRE"
    assert tts_calls[-1][0].startswith("<shouting>") and tts_calls[-1][1] == alerts.settings.alert_voice
    # voice-clone failure falls back to Timbre
    fail["vc"] = True
    assert asyncio.run(alerts.alert_audio(2, "en-IN", fam)) == b"TIMBRE"
    # a deleted voice also falls back
    enrol.delete(v["id"])
    assert asyncio.run(alerts.alert_audio(2, "ta-IN", fam)) == b"TIMBRE"


def test_honeypot_consent_is_stored_with_the_voice(data):
    c = TestClient(main.app)
    r = c.post("/api/voices", data={"name": "Kamla", "consent": "true", "honeypot": "true"},
               files={"audio": ("a.wav", wav_of(8))})
    assert r.status_code == 200 and r.json()["honeypot"] is True
    assert enrol.load(r.json()["id"])["honeypot"] is True
    assert c.get("/api/voices").json()[0]["honeypot"] is True


def test_oversized_upload_rejected_before_ffmpeg(data):
    with pytest.raises(enrol.EnrolError, match="too large"):
        asyncio.run(enrol.enrol(b"\0" * (enrol.MAX_UPLOAD_BYTES + 1), "Rahul", consent=True))
    with pytest.raises(enrol.EnrolError, match="5-30 seconds"):
        asyncio.run(enrol.enrol(wav_of(45), "Rahul", consent=True))  # long files are cut at 31 s, then refused
    assert data == []


def test_delete_purges_cloned_audio_and_bad_files_are_skipped(data, monkeypatch, tmp_path):
    v = asyncio.run(enrol.enrol(wav_of(8), "Rahul", consent=True))

    async def fake_vc(text, embedding):
        return b"VC"

    monkeypatch.setattr(gnani, "vc_tts", fake_vc)
    assert asyncio.run(alerts.alert_audio(2, "hi-IN", f"family:{v['id']}")) == b"VC"
    assert any(enrol.cache_dir(v["id"]).iterdir())
    (tmp_path / "embeddings" / "0123456789ab.json").write_text("{half", encoding="utf-8")
    assert enrol.voices() == [v] and enrol.load("0123456789ab") is None
    enrol.delete(v["id"])
    assert not enrol.cache_dir(v["id"]).exists()


def test_voice_clone_network_error_falls_back_to_timbre(data, monkeypatch):
    v = asyncio.run(enrol.enrol(wav_of(8), "Rahul", consent=True))

    async def broken_vc(text, embedding):
        raise TimeoutError("vc timed out")

    async def fake_tts(text, voice, language):
        return b"TIMBRE"

    monkeypatch.setattr(gnani, "vc_tts", broken_vc)
    monkeypatch.setattr(gnani, "tts", fake_tts)
    assert asyncio.run(alerts.alert_audio(2, "hi-IN", f"family:{v['id']}")) == b"TIMBRE"
