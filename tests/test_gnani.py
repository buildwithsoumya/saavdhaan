import asyncio
import base64
import json

import httpx
import pytest
import websockets

from app import gnani


class FakeWS:
    """Stands in for a Gnani realtime session."""

    def __init__(self, headers):
        self.headers = headers
        self.sent = []
        self.closed = False
        self.inbox = asyncio.Queue()

    async def send(self, data):
        if self.closed:
            raise websockets.ConnectionClosed(None, None)
        self.sent.append(data)

    async def close(self):
        self.closed = True
        await self.inbox.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        msg = await self.inbox.get()
        if msg is None:
            raise StopAsyncIteration
        return msg

    def frames(self):
        return [s for s in self.sent if isinstance(s, bytes)]

    def controls(self):
        return [json.loads(s) for s in self.sent if isinstance(s, str)]


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def make_stream(**kw):
    sockets, events, clock = [], [], Clock()

    async def connect(url, additional_headers):
        assert url == gnani.STT_URL
        ws = FakeWS(additional_headers)
        sockets.append(ws)
        return ws

    async def on_event(msg):
        events.append(msg)

    stream = gnani.SttStream(on_event, connect=connect, clock=clock, **kw)
    return stream, sockets, events, clock


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def test_headers_v3_codemix_vad_and_escaped_keyterms(monkeypatch):
    monkeypatch.setattr(gnani.settings, "gnani_api_key", "k")
    h = gnani.stt_headers(keyterms=["OTP", "गिरफ्तार"], sample_rate=8000)
    assert h["x-api-key-id"] == "k"
    assert h["model"] == "prisma-v3.0" and h["format"] == "codemix"
    assert h["language_code"] == "indic-auto" and h["x-sample-rate"] == "8000"
    assert (h["x-vad-threshold"], h["x-min-speech-ms"], h["x-min-silence-ms"]) == ("0.7", "200", "500")
    assert h["keyterms"].isascii() and json.loads(h["keyterms"]) == ["OTP", "गिरफ्तार"]
    assert "previous_text" not in h


def test_headers_previous_text_is_utf8_trimmed_and_sanitised():
    h = gnani.stt_headers(previous_text="x" * 700 + "\r\nआप <|गिरफ्तार|>")
    raw = h["previous_text"].encode("latin-1").decode("utf-8")  # what goes on the wire
    assert len(raw) <= 600 and raw.endswith("आप गिरफ्तार")
    assert "\n" not in raw and "<|" not in raw
    websockets.datastructures.Headers(previous_text=h["previous_text"])  # accepted by the client


def test_headers_v25_uses_single_word_bias():
    h = gnani.stt_headers(model="prisma-v2.5", keyterms=["OTP", "safe account", "CBI"], language="hi-IN")
    assert h["format"] == "verbatim" and "keyterms" not in h
    assert json.loads(h["bias_list"]) == ["OTP", "CBI"] and h["bias_score"] == "1.0"


def test_audio_is_sent_in_1024_byte_frames():
    async def run():
        stream, sockets, _, _ = make_stream()
        await stream.start()
        await stream.send_audio(b"\x01" * 1000)
        assert sockets[0].frames() == []
        await stream.send_audio(b"\x02" * 1100)
        assert [len(f) for f in sockets[0].frames()] == [1024, 1024]
        await stream.commit()
        assert sockets[0].controls() == [{"type": "commit"}]
    asyncio.run(run())


def test_transcripts_and_errors_reach_caller_and_build_history():
    async def run():
        stream, sockets, events, _ = make_stream()
        await stream.start()
        await sockets[0].inbox.put(json.dumps({"type": "transcript", "text": "main CBI se bol raha hoon"}))
        await sockets[0].inbox.put(json.dumps({"type": "error", "message": "boom"}))
        await settle()
        assert [e["type"] for e in events] == ["transcript", "error"]
        assert stream.history == "main CBI se bol raha hoon"
    asyncio.run(run())


def test_rotates_before_15_minute_cap_carrying_context(monkeypatch):
    monkeypatch.setattr(gnani.SttStream, "_close_later", lambda self, ws, grace_s=0: ws.close())

    async def run():
        stream, sockets, events, clock = make_stream(keyterms=["CBI"])
        await stream.start()
        await sockets[0].inbox.put(json.dumps({"type": "transcript", "text": "aapke naam pe FIR hai"}))
        await settle()
        await stream.update_keyterms(["OTP", "UPI"])
        clock.t = gnani.ROTATE_AFTER_S - 1
        await stream.send_audio(b"\0" * 1024)
        assert len(sockets) == 1
        clock.t = gnani.ROTATE_AFTER_S
        await stream.send_audio(b"\0" * 1024)
        await settle()
        assert len(sockets) == 2 and stream.sessions == 2
        old, new = sockets
        assert old.controls()[-1] == {"type": "commit"} and old.closed
        assert new.headers["previous_text"] == "aapke naam pe FIR hai"
        assert json.loads(new.headers["keyterms"]) == ["OTP", "UPI"]
        assert len(new.frames()) == 1  # audio continues on the new session
        # transcripts from the new session keep extending the same history
        await new.inbox.put(json.dumps({"type": "transcript", "text": "OTP batao"}))
        await settle()
        assert events[-1]["text"] == "OTP batao"
        assert stream.history.endswith("FIR hai OTP batao")
    asyncio.run(run())


def test_reconnects_after_server_closes_session():
    async def run():
        stream, sockets, _, clock = make_stream()
        await stream.start()
        clock.t = 60  # the session ran for a minute
        await sockets[0].close()  # e.g. code 1001 after the session cap
        await settle()
        clock.t += 1.1  # reconnects after a short pause
        await stream.send_audio(b"\0" * 2048)
        assert len(sockets) == 2 and len(sockets[1].frames()) == 2
    asyncio.run(run())


def test_update_keyterms_sends_bias_frame():
    async def run():
        stream, sockets, _, _ = make_stream()
        await stream.start()
        await stream.update_keyterms(["OTP", "safe account"])
        assert sockets[0].controls() == [{"type": "update_settings", "bias": {"list": ["OTP", "safe account"]}}]
    asyncio.run(run())


def mock(handler, monkeypatch):
    monkeypatch.setattr(gnani, "transport", httpx.MockTransport(handler))
    monkeypatch.setattr(gnani.asyncio, "sleep", _no_sleep)


async def _no_sleep(_):
    return None


def test_tts_request_body(monkeypatch):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["key"] = req.headers["x-api-key-id"]
        return httpx.Response(200, content=b"RIFFwav")

    mock(handler, monkeypatch)
    monkeypatch.setattr(gnani.settings, "gnani_api_key", "k")
    audio = asyncio.run(gnani.tts("<slow> dhyaan se", voice="Shivani", language="hi-IN"))
    assert audio == b"RIFFwav" and seen["key"] == "k"
    assert seen["body"]["model"] == "timbre-v3.0" and seen["body"]["language"] == "hi-IN"
    assert seen["body"]["audio_config"]["container"] == "wav"


def test_429_is_retried_then_errors_raise(monkeypatch):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429) if len(calls) < 3 else httpx.Response(200, content=b"ok")

    mock(handler, monkeypatch)
    assert asyncio.run(gnani.tts("x", voice="Shivani", language="hi-IN")) == b"ok"
    assert len(calls) == 3

    mock(lambda req: httpx.Response(400, json={"success": False, "message": "bad voice"}), monkeypatch)
    with pytest.raises(gnani.GnaniError, match="bad voice"):
        asyncio.run(gnani.tts("x", voice="Nope", language="hi-IN"))


def test_tts_sse_yields_decoded_chunks(monkeypatch):
    body = (
        'event: start\ndata: {"status": "streaming_started"}\n\n'
        f'event: chunk\ndata: {{"chunk_index": 1, "audio": "{base64.b64encode(b"ab").decode()}", "is_final": false}}\n\n'
        f'event: chunk\ndata: {{"chunk_index": 2, "audio": "{base64.b64encode(b"cd").decode()}", "is_final": false}}\n\n'
        'event: complete\ndata: {"chunk_index": 3, "audio": "", "is_final": true}\n\n'
    )
    mock(lambda req: httpx.Response(200, text=body), monkeypatch)

    async def run():
        return [c async for c in gnani.tts_sse("x", voice="Shivani", language="hi-IN")]
    assert asyncio.run(run()) == [b"ab", b"cd"]


def test_voice_clone_embed_then_synthesize(monkeypatch):
    emb = {"embedding": "abc", "shape": [1, 768], "dtype": "torch.bfloat16"}
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"success": True, "data": {"voice_clone_embedding": emb}})
        return httpx.Response(200, content=b"wav")

    mock(handler, monkeypatch)
    got = asyncio.run(gnani.clone_embedding(b"RIFF..."))
    assert got == emb and b'name="audio_file"' in seen[0].content
    assert asyncio.run(gnani.vc_tts("Maa, main hoon", got)) == b"wav"
    body = json.loads(seen[1].content)
    assert body["model"] == "vachana-vc-v1" and body["speaker_embedding"] == emb


def test_batch_transcribe_flow(monkeypatch):
    statuses = iter(["QUEUED", "IN_PROGRESS", "COMPLETED"])
    doc = {"segments": [{"speaker_id": 1, "text": "hello", "start_time": 0.0, "end_time": 1.0}]}
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path))
        p = req.url.path
        if p == "/stt/v3/batch/jobs":
            assert b'"with_diarization": true' in req.content and b"application/json" in req.content
            return httpx.Response(201, json={"job_id": "j1", "status": "CREATED"})
        if p.endswith("/start"):
            return httpx.Response(202, json={"status": "STARTING"})
        if p.endswith("/files"):
            return httpx.Response(200, json={"data": [{"transcript_url": "https://s3.example/t.json"}]})
        if p == "/stt/v3/batch/jobs/j1":
            return httpx.Response(200, json={"status": next(statuses)})
        return httpx.Response(200, json=doc)

    mock(handler, monkeypatch)
    assert asyncio.run(gnani.batch_transcribe(b"RIFF")) == doc
    assert ("POST", "/stt/v3/batch/jobs/j1/start") in seen and seen[-1] == ("GET", "/t.json")


def test_batch_failure_raises(monkeypatch):
    def handler(req):
        if req.url.path == "/stt/v3/batch/jobs":
            return httpx.Response(201, json={"job_id": "j1"})
        return httpx.Response(200, json={"status": "FAILED"})

    mock(handler, monkeypatch)
    with pytest.raises(gnani.GnaniError, match="FAILED"):
        asyncio.run(gnani.batch_transcribe(b"RIFF"))


def test_previous_text_reaches_the_wire_as_utf8():
    h = websockets.datastructures.Headers()
    h["previous_text"] = gnani.stt_headers(previous_text="नमस्ते जी")["previous_text"]
    assert "previous_text: नमस्ते जी\r\n".encode("utf-8") in h.serialize()


def test_short_lived_sessions_back_off_instead_of_storming():
    async def run():
        stream, sockets, _, clock = make_stream()
        await stream.start()
        await sockets[0].close()  # server accepts, then closes at once (e.g. concurrency cap)
        await settle()
        for _ in range(20):  # 20 frames at the same instant: no reconnect storm
            await stream.send_audio(b"\0" * 1024)
        assert len(sockets) == 1
        clock.t += 1.1  # after the 1 s backoff it reconnects once
        await stream.send_audio(b"\0" * 1024)
        assert len(sockets) == 2
        assert 0 < len(sockets[1].frames()) <= gnani.RECONNECT_BUFFER_BYTES // 1024 + 1
        await sockets[1].close()
        await settle()
        clock.t += 1.1
        await stream.send_audio(b"\0" * 1024)
        assert len(sockets) == 2  # second short session doubles the wait to 2 s
    asyncio.run(run())


def test_reader_survives_bad_frames_and_handler_errors():
    async def run():
        calls = []

        async def on_event(msg):
            calls.append(msg)
            if msg.get("boom"):
                raise RuntimeError("caller bug")

        async def connect(url, additional_headers):
            return ws

        ws = FakeWS({})
        stream = gnani.SttStream(on_event, connect=connect)
        await stream.start()
        for raw in ("not json", json.dumps({"type": "x", "boom": True}), json.dumps({"type": "transcript", "text": "ok"})):
            await ws.inbox.put(raw)
        await settle()
        assert calls[-1]["text"] == "ok" and not stream._dead
    asyncio.run(run())


def test_close_flushes_last_utterance_and_closes_rotated_sessions(monkeypatch):
    async def run():
        stream, sockets, events, clock = make_stream()
        await stream.start()
        clock.t = gnani.ROTATE_AFTER_S
        task = asyncio.create_task(stream.send_audio(b"\0" * 1024))  # rotation waits for the flush
        await settle()
        await sockets[0].inbox.put(json.dumps({"type": "transcript", "text": "aakhri baat"}))
        await task
        assert sockets[1].headers["previous_text"] == "aakhri baat"  # flushed text made it into context
        closing = asyncio.create_task(stream.close())
        await settle()
        await sockets[1].inbox.put(json.dumps({"type": "transcript", "text": "phone rakhiye"}))
        await closing
        assert events[-1]["text"] == "phone rakhiye"
        assert sockets[0].closed and sockets[1].closed
    asyncio.run(run())


def test_network_errors_become_gnani_errors(monkeypatch):
    def handler(req):
        raise httpx.ConnectTimeout("slow", request=req)

    mock(handler, monkeypatch)
    with pytest.raises(gnani.GnaniError, match="failed"):
        asyncio.run(gnani.tts("x", voice="Shivani", language="hi-IN"))


def test_tts_sse_retries_429(monkeypatch):
    calls = []
    body = f'event: chunk\ndata: {{"audio": "{base64.b64encode(b"ok").decode()}"}}\n\n'

    def handler(req):
        calls.append(1)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, text=body)

    mock(handler, monkeypatch)

    async def run():
        return [c async for c in gnani.tts_sse("x", voice="Shivani", language="hi-IN")]
    assert asyncio.run(run()) == [b"ok"] and len(calls) == 2


def test_batch_gives_up_after_deadline(monkeypatch):
    def handler(req):
        if req.url.path == "/stt/v3/batch/jobs":
            return httpx.Response(201, json={"job_id": "j1"})
        return httpx.Response(200, json={"status": "QUEUED"})

    mock(handler, monkeypatch)
    monkeypatch.setattr(gnani, "BATCH_TIMEOUT_S", -1)
    with pytest.raises(gnani.GnaniError, match="still QUEUED"):
        asyncio.run(gnani.batch_transcribe(b"RIFF"))
