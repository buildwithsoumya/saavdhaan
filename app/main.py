"""Saavdhaan backend: serves the page and bridges browser mic audio to Gnani STT.

Browser --(16 kHz PCM over WS)--> backend --(headers + 1024-byte frames)--> Gnani
Browsers cannot set WebSocket headers, so the API key lives only here.
"""
import asyncio
import hmac
import json
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import alerts, enrol, gnani, report
from app.config import settings
from app.tracker import Tracker, keyterms

WEB = Path(__file__).parent.parent / "web"
app = FastAPI()


@dataclass
class Call:
    """Per-call state, kept across browser reconnects (same call id)."""
    tracker: Tracker = field(default_factory=Tracker)
    alerted: int = 0
    pcm: bytearray = field(default_factory=bytearray)
    socket: WebSocket | None = None  # the browser connection currently serving this call


CALLS: dict[str, Call] = {}
MAX_CALLS = 50  # ponytail: in-memory call state, oldest dropped first; a store if this ever runs as a service
CALL_ID = re.compile(r"[A-Za-z0-9-]{1,64}")


def token_ok(token: str | None) -> bool:
    if not settings.app_token:
        return True
    # bytes compare: compare_digest rejects non-ASCII str, which would turn a junk token into a 500
    return hmac.compare_digest((token or "").encode(), settings.app_token.encode())


@app.middleware("http")
async def require_token(request: Request, call_next):
    """The page itself is public; anything that spends credits or shows call data needs the token."""
    if request.url.path.startswith(("/api/", "/reports/")):
        token = request.query_params.get("token") or request.headers.get("x-app-token")
        if not token_ok(token):
            return JSONResponse({"detail": "missing or wrong access token"}, status_code=401)
    return await call_next(request)


def save_recording(call_id: str, pcm: bytes) -> Path:
    path = Path(settings.data_dir) / "calls" / f"{call_id}.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return path


@app.websocket("/ws")
async def guardian(ws: WebSocket, call: str, voice: str = "", record: int = 0, token: str = ""):
    await ws.accept()  # accept first: a close before accept reaches the browser as 1006, not our code
    if not token_ok(token):
        await ws.close(code=4401)
        return
    if not CALL_ID.fullmatch(call):  # the id becomes a file name for recordings
        await ws.close(code=1008)
        return
    state = CALLS.pop(call, None) or Call()
    CALLS[call] = state  # most recently used last
    while len(CALLS) > MAX_CALLS:
        CALLS.pop(next(iter(CALLS)))
    if state.socket is not None:  # a reconnect while the old socket is half-dead: one stream per call
        try:
            await state.socket.close()
        except Exception:
            pass
    state.socket = ws
    send_lock = asyncio.Lock()
    alert_tasks = set()

    async def send(payload):
        """Send to the browser; a vanished browser is not an error here, the receive loop ends the call."""
        try:
            async with send_lock:
                if isinstance(payload, bytes):
                    await ws.send_bytes(payload)
                else:
                    await ws.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    async def play_alert(level, lang):
        try:
            audio = await alerts.alert_audio(level, lang, voice or None)
        except Exception as e:  # network, cache or synthesis failure: let the next transcript retry
            state.alerted = min(state.alerted, level - 1)
            await send({"type": "error", "message": f"alert audio failed: {e}"})
            return
        await send({"type": "alert", "level": level, "text": alerts.alert_text(level, lang)})
        await send(audio)

    async def on_event(msg):
        kind = msg.get("type")
        if kind == "processing":
            await send({"type": "speech_end"})
        elif kind == "transcript" and msg.get("text"):
            lang = msg.get("detected_language_code")
            state.tracker.feed(msg["text"])
            level = state.tracker.level()
            await send({"type": "transcript", "text": msg["text"], "lang": lang,
                        "explain": state.tracker.explain()})
            if state.tracker.take_follow_signal():
                await stream.update_keyterms(keyterms("extraction"))
                await send({"type": "info", "message": "keyterms switched to extraction stage"})
            if level > state.alerted:
                state.alerted = level
                task = asyncio.create_task(play_alert(level, lang))
                alert_tasks.add(task)
                task.add_done_callback(alert_tasks.discard)
        elif kind == "error":
            await send({"type": "error", "message": msg.get("message", "STT error")})

    stream = gnani.SttStream(on_event, keyterms=keyterms("extraction" if state.tracker.followed else "base"))
    try:
        await stream.start()
    except Exception as e:  # bad key, no network, concurrency cap
        await send({"type": "error", "message": f"could not reach speech service: {e}"})
        await ws.close()
        return
    await send({"type": "ready", "explain": state.tracker.explain()})
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes"):
                await stream.send_audio(msg["bytes"])
                if record:
                    state.pcm += msg["bytes"]
            elif msg.get("text"):
                try:
                    cmd = json.loads(msg["text"])
                except ValueError:
                    continue
                if cmd.get("type") == "stop":
                    break
    except WebSocketDisconnect:
        pass
    finally:
        for t in list(alert_tasks):
            t.cancel()
        if state.socket is ws:
            state.socket = None
        if record and state.pcm:  # saved first: the report button may be pressed the moment the call stops
            await asyncio.to_thread(save_recording, call, bytes(state.pcm))
        await stream.close()


@app.post("/api/voices")
async def enrol_voice(audio: UploadFile = File(...), name: str = Form(...), consent: bool = Form(False)):
    try:
        return await enrol.enrol(await audio.read(enrol.MAX_UPLOAD_BYTES + 1), name, consent)
    except enrol.EnrolError as e:
        raise HTTPException(400, str(e))
    except gnani.GnaniError as e:
        raise HTTPException(502, f"voice service failed: {e}")


@app.get("/api/voices")
def list_voices():
    return enrol.voices()


@app.delete("/api/voices/{voice_id}")
def delete_voice(voice_id: str):
    if not enrol.delete(voice_id):
        raise HTTPException(404, "no such voice")
    return {"deleted": voice_id}


REPORTS_IN_FLIGHT: set[str] = set()


def _call_file(folder: str, call_id: str, ext: str) -> Path:
    if not CALL_ID.fullmatch(call_id):
        raise HTTPException(400, "bad call id")
    return Path(settings.data_dir) / folder / f"{call_id}.{ext}"


@app.post("/api/report/{call_id}")
async def make_report(call_id: str):
    out = _call_file("reports", call_id, "html")
    wav = _call_file("calls", call_id, "wav")
    if out.exists():  # batch STT costs credits: one report per call
        return {"url": f"/reports/{call_id}"}
    if not wav.exists():
        raise HTTPException(404, "this call was not recorded (recording is opt-in)")
    if call_id in REPORTS_IN_FLIGHT:
        raise HTTPException(409, "the report is already being made")
    REPORTS_IN_FLIGHT.add(call_id)
    try:
        await report.build(wav)
    except Exception as e:  # ffmpeg, network, batch failure: always a readable JSON error
        raise HTTPException(502, f"report failed: {e}")
    finally:
        REPORTS_IN_FLIGHT.discard(call_id)
    return {"url": f"/reports/{call_id}"}


@app.get("/reports/{call_id}")
def get_report(call_id: str):
    path = _call_file("reports", call_id, "html")
    if not path.exists():
        raise HTTPException(404, "no report yet")
    return FileResponse(path, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                                       "Referrer-Policy": "no-referrer"})


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
