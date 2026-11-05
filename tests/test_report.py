import asyncio
import re
import wave

import pytest
from fastapi.testclient import TestClient

from app import gnani, main, report

DOC = {"segments": [
    {"speaker_id": 1, "start_time": 0.5, "end_time": 3.0, "text": "मैं CBI से inspector बोल रहा हूँ"},
    {"speaker_id": 2, "start_time": 3.2, "end_time": 4.0, "text": "जी, क्या बात है?"},
    {"speaker_id": 1, "start_time": 4.5, "end_time": 9.0, "text": "आपके नाम पर arrest warrant है, किसी को मत बताना"},
    {"speaker_id": 1, "start_time": 9.5, "end_time": 13.0, "text": "तुरंत safe account में transfer कीजिए"},
    {"speaker_id": 2, "start_time": 13.5, "end_time": 14.0, "text": ""},
]}


def test_analyse_finds_caller_and_stage_order():
    a = report.analyse(DOC)
    assert a["caller"] == 1 and a["level"] == 3
    assert [s for s, _ in sorted(a["first"].items(), key=lambda kv: kv[1].t)] == ["S1", "S2", "S3", "S4", "S5"]
    assert len(a["lines"]) == 4  # empty segment dropped


def test_html_has_timeline_speakers_and_placeholder_complaint():
    page = report.render_html(report.analyse(DOC), "demo.wav")
    assert "Caller (suspected):" in page and "Speaker 2:" in page and "00:09" in page
    assert all(p in page for p in ("[Your full name]", "[Your phone number]"))
    assert "“safe account”" in page and "1930" in page
    assert not re.search(r"\d{10}", page)  # no phone- or account-like numbers


def test_no_speech_report_still_renders():
    page = report.render_html(report.analyse({"segments": []}), "empty.wav")
    assert "No scam stages detected" in page and "L0" in page


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)

    async def fake_batch(audio, filename="call.wav", **kw):
        assert audio[:4] == b"RIFF"
        return DOC

    monkeypatch.setattr(gnani, "batch_transcribe", fake_batch)
    return TestClient(main.app)


def test_report_endpoints(client, tmp_path):
    assert client.post("/api/report/nope-1").status_code == 404  # not recorded
    assert client.post("/api/report/a.b").status_code == 400  # only letters, digits, dashes
    (tmp_path / "calls").mkdir()
    with wave.open(str(tmp_path / "calls" / "abc-123.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000)
    r = client.post("/api/report/abc-123")
    assert r.status_code == 200 and r.json() == {"url": "/reports/abc-123"}
    page = client.get("/reports/abc-123")
    assert page.status_code == 200 and "Draft complaint" in page.text


def test_build_writes_report(client, tmp_path):
    src = tmp_path / "demo.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 8000)
    out = asyncio.run(report.build(src))
    assert out.name == "demo.html" and "Caller (suspected)" in out.read_text(encoding="utf-8")
