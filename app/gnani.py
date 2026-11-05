"""Clients for the Gnani speech APIs (https://docs.gnani.ai).

Realtime STT (Prisma), TTS (Timbre, REST and SSE), voice cloning and batch STT.
"""
import asyncio
import base64
import json
import time

import httpx
import websockets

from app.config import settings

STT_URL = "wss://api.vachana.ai/stt/v3/stream"
API_URL = "https://api.vachana.ai"
FRAME_BYTES = 1024  # 512 samples of 16-bit mono PCM, required by the realtime API
ROTATE_AFTER_S = 14 * 60  # sessions are capped at 15 minutes; reconnect before that
PREVIOUS_TEXT_CHARS = 600  # the server keeps only the last 600 characters
TERMINAL = {"COMPLETED", "PARTIAL_FAILURE", "FAILED", "START_FAILED", "CANCELLED"}

transport: httpx.AsyncBaseTransport | None = None  # tests swap in httpx.MockTransport


class GnaniError(Exception):
    pass


def utf8_header(value: str) -> str:
    """Raw UTF-8 header value. `websockets` only takes latin-1 strings and writes
    them back out as latin-1, so this round-trip puts the UTF-8 bytes on the wire."""
    return value.encode("utf-8").decode("latin-1")


def stt_headers(model="prisma-v3.0", sample_rate=16000, keyterms=(), previous_text="",
                language="indic-auto") -> dict:
    h = {
        "x-api-key-id": settings.gnani_api_key,
        "model": model,
        "language_code": language,
        "x-sample-rate": str(sample_rate),
        # telephony VAD preset from the docs: a speakerphone call is a noisy line
        "x-vad-threshold": "0.7",
        "x-min-speech-ms": "200",
        "x-min-silence-ms": "500",
        "x-idle-timeout-sec": "900",
    }
    if model == "prisma-v3.0":
        h["format"] = "codemix"  # English loanwords (OTP, police, CBI) stay in Latin
        if keyterms:
            h["keyterms"] = json.dumps(list(keyterms)[:200])  # ensure_ascii escapes Indic text
        prev = previous_text.replace("\n", " ").replace("\r", " ")
        prev = prev.replace("<|", "").replace("|>", "").strip()[-PREVIOUS_TEXT_CHARS:]
        if prev:
            h["previous_text"] = utf8_header(prev)
    else:
        # Prisma v2.5 has no codemix/keyterms; word boosting takes single words only
        h["format"] = "verbatim"
        words = [k for k in keyterms if k.isalpha()][:100]
        if words:
            h["bias_list"] = json.dumps(words)
            h["bias_score"] = "1.0"
    return h


class SttStream:
    """One logical realtime STT stream for a whole call.

    Gnani sessions end at 15 minutes and scam calls last hours, so this rotates to a
    fresh session every ~14 minutes (and after any unexpected close), passing the
    transcript so far as `previous_text` and the current keyterms. Callers see one
    continuous stream of server events through `on_event`.
    """

    def __init__(self, on_event, *, model="prisma-v3.0", sample_rate=16000, keyterms=(),
                 language="indic-auto", connect=websockets.connect, clock=time.monotonic):
        self.on_event = on_event
        self.model, self.sample_rate, self.language = model, sample_rate, language
        self.keyterms = list(keyterms)
        self.history = ""
        self.sessions = 0
        self._connect, self._clock = connect, clock
        self._ws = None
        self._opened_at = 0.0
        self._dead = False
        self._buf = b""
        self._tasks = set()

    async def start(self):
        await self._open()

    async def _open(self):
        headers = stt_headers(self.model, self.sample_rate, self.keyterms, self.history, self.language)
        for attempt in range(3):
            try:
                ws = await self._connect(STT_URL, additional_headers=headers)
                break
            except (OSError, websockets.WebSocketException):
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
        self._ws, self._opened_at, self._dead = ws, self._clock(), False
        self.sessions += 1
        self._spawn(self._read(ws))

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _read(self, ws):
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "transcript" and msg.get("text"):
                    self.history = (self.history + " " + msg["text"]).strip()[-2000:]
                await self.on_event(msg)
        except websockets.ConnectionClosed:
            pass
        finally:
            if ws is self._ws:
                self._dead = True  # next send_audio reconnects

    async def rotate(self):
        """Swap to a new session; the old one is flushed and closed in the background."""
        old = self._ws
        if not self._dead:
            try:
                await old.send(json.dumps({"type": "commit"}))
            except websockets.ConnectionClosed:
                pass
        await self._open()
        self._spawn(self._close_later(old))

    async def _close_later(self, ws, grace_s=3.0):
        await asyncio.sleep(grace_s)  # let the committed utterance come back
        await ws.close()

    async def send_audio(self, pcm: bytes):
        self._buf += pcm
        if self._dead or self._clock() - self._opened_at >= ROTATE_AFTER_S:
            await self.rotate()
        while len(self._buf) >= FRAME_BYTES:
            frame, self._buf = self._buf[:FRAME_BYTES], self._buf[FRAME_BYTES:]
            try:
                await self._ws.send(frame)
            except websockets.ConnectionClosed:
                self._buf = frame + self._buf
                self._dead = True
                return

    async def commit(self):
        await self._ws.send(json.dumps({"type": "commit"}))

    async def update_keyterms(self, terms):
        """Change keyterms mid-session (applies from the next segment) and keep them for reconnects."""
        self.keyterms = list(terms)[:200]
        bias = self.keyterms if self.model == "prisma-v3.0" else [t for t in self.keyterms if t.isalpha()][:100]
        await self._ws.send(json.dumps({"type": "update_settings", "bias": {"list": bias}}))

    async def close(self):
        if self._ws is not None and not self._dead:
            try:
                await self.commit()
            except websockets.ConnectionClosed:
                pass
            await self._ws.close()
        for t in list(self._tasks):
            t.cancel()


def _client(timeout=60.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API_URL, headers={"X-API-Key-ID": settings.gnani_api_key},
                             timeout=timeout, transport=transport)


async def _request(client, method, url, **kw) -> httpx.Response:
    """One request, retrying 429s (accounts may allow one active request at a time)."""
    for attempt in range(6):
        r = await client.request(method, url, **kw)
        if r.status_code != 429:
            break
        await asyncio.sleep(min(2 ** attempt, 20))
    if r.status_code >= 400:
        raise GnaniError(f"{method} {url} -> {r.status_code}: {r.text[:300]}")
    return r


def _audio_config(sample_rate, container="wav"):
    return {"sample_rate": sample_rate, "num_channels": 1, "sample_width": 2,
            "encoding": "linear_pcm", "container": container}


async def tts(text, *, voice, language, model="timbre-v3.0", sample_rate=16000, speed=1.0) -> bytes:
    """Timbre TTS, returns a WAV file."""
    body = {"text": text, "voice": voice, "model": model, "language": language, "speed": speed,
            "audio_config": _audio_config(sample_rate)}
    async with _client() as c:
        return (await _request(c, "POST", "/api/v1/tts/inference", json=body)).content


async def tts_sse(text, *, voice, language, model="timbre-v3.0", sample_rate=16000):
    """Streaming Timbre TTS: yields raw 16-bit PCM chunks as they are synthesized."""
    body = {"text": text, "voice": voice, "model": model, "language": language,
            "audio_config": _audio_config(sample_rate, container="raw")}
    async with _client() as c, c.stream("POST", "/api/v1/tts/sse", json=body) as r:
        if r.status_code >= 400:
            raise GnaniError(f"tts sse -> {r.status_code}: {(await r.aread())[:300]!r}")
        event = ""
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
                if event == "error":
                    raise GnaniError(f"tts sse error: {data}")
                if data.get("audio"):
                    yield base64.b64decode(data["audio"])


async def clone_embedding(audio: bytes, filename="reference.wav") -> dict:
    """Speaker embedding from 5-30 s of reference audio."""
    async with _client() as c:
        r = await _request(c, "POST", "/api/v1/tts/voice-clone/embeddings",
                           files={"audio_file": (filename, audio)})
    return r.json()["data"]["voice_clone_embedding"]


async def vc_tts(text, embedding: dict, sample_rate=16000) -> bytes:
    """TTS in a cloned voice, returns a WAV file."""
    body = {"text": text, "model": "vachana-vc-v1", "speaker_embedding": embedding,
            "audio_config": _audio_config(sample_rate)}
    async with _client() as c:
        return (await _request(c, "POST", "/api/v1/tts/inference", json=body)).content


async def batch_transcribe(audio: bytes, filename="call.wav", *, num_speakers=2, poll_s=10.0) -> dict:
    """Batch STT with speaker separation. Returns the transcript document
    (`segments` with `speaker_id`, `start_time`, `end_time`, `text`)."""
    config = {"model": "prisma-v3.0", "language_code": "indic-auto", "format": "codemix",
              "with_diarization": True, "num_speakers": num_speakers}
    async with _client(timeout=120.0) as c:
        # config is a JSON form part (curl: -F 'config=...;type=application/json')
        r = await _request(c, "POST", "/stt/v3/batch/jobs", files=[
            ("config", (None, json.dumps(config), "application/json")),
            ("files", (filename, audio, "audio/wav")),
        ])
        job = r.json()["job_id"]
        await _request(c, "POST", f"/stt/v3/batch/jobs/{job}/start")
        while True:
            status = (await _request(c, "GET", f"/stt/v3/batch/jobs/{job}")).json()["status"]
            if status in TERMINAL:
                break
            await asyncio.sleep(poll_s)  # docs: poll no faster than every 10 s
        if status not in ("COMPLETED", "PARTIAL_FAILURE"):
            raise GnaniError(f"batch job {job} ended {status}")
        files = (await _request(c, "GET", f"/stt/v3/batch/jobs/{job}/files",
                                params={"status": "COMPLETED"})).json()["data"]
        if not files:
            raise GnaniError(f"batch job {job} has no completed files")
        async with httpx.AsyncClient(timeout=60.0, transport=transport) as plain:  # presigned URL, no API key
            doc = await plain.get(files[0]["transcript_url"])
            doc.raise_for_status()
            return doc.json()
