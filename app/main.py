"""Saavdhaan backend: serves the page and bridges browser mic audio to Gnani STT.

Browser --(16 kHz PCM over WS)--> backend --(headers + 1024-byte frames)--> Gnani
Browsers cannot set WebSocket headers, so the API key lives only here.
"""
import asyncio
import hmac
import json
import wave
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import alerts, enrol, gnani, honeypot, report
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
    honeypot: bool = False
    history: list = field(default_factory=list)  # honeypot chat: caller = user, persona = assistant


CALLS: dict[str, Call] = {}


def token_ok(token: str | None) -> bool:
    return not settings.app_token or hmac.compare_digest(token or "", settings.app_token)


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
    if not token_ok(token):
        await ws.close(code=4401)
        return
    await ws.accept()
    state = CALLS.setdefault(call, Call())
    send_lock = asyncio.Lock()
    alert_tasks = set()

    async def send(payload):
        async with send_lock:
            if isinstance(payload, bytes):
                await ws.send_bytes(payload)
            else:
                await ws.send_text(json.dumps(payload, ensure_ascii=False))

    say_lock = asyncio.Lock()

    def spawn(coro):
        task = asyncio.create_task(coro)
        alert_tasks.add(task)
        task.add_done_callback(alert_tasks.discard)

    async def set_honeypot(on: bool, reason: str):
        state.honeypot = on
        await send({"type": "honeypot", "on": on, "reason": reason})
        if not on and state.history:
            log = Path(settings.data_dir) / "honeypot" / f"{call}.json"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(json.dumps(state.history, ensure_ascii=False, indent=1), encoding="utf-8")

    async def persona_reply(text, lang):
        """Answer the caller in the persona voice; one reply at a time."""
        async with say_lock:
            if not state.honeypot:
                return
            state.history.append({"role": "user", "content": text})
            line = await honeypot.reply(state.history, lang)
            state.history.append({"role": "assistant", "content": line})
            try:
                audio = await gnani.tts(line, voice=settings.honeypot_voice,
                                        language=f"{honeypot.lang_of(lang)}-IN", speed=0.9)
            except gnani.GnaniError as e:
                await send({"type": "error", "message": f"honeypot voice failed: {e}"})
                return
            await send({"type": "say", "text": line})
            await send(audio)
            exchanges = sum(m["role"] == "assistant" for m in state.history)
            if honeypot.evidence_complete(state.tracker, exchanges):
                await set_honeypot(False, "evidence")

    async def play_alert(level, lang):
        if state.honeypot:
            return  # the caller would hear it; the screen still shows the level
        try:
            audio = await alerts.alert_audio(level, lang, voice or None)
        except gnani.GnaniError as e:
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
                spawn(play_alert(level, lang))
            if state.honeypot:
                spawn(persona_reply(msg["text"], lang))
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
                cmd = json.loads(msg["text"])
                if cmd.get("type") == "stop":
                    break
                if cmd.get("type") == "honeypot":
                    if not cmd.get("on"):
                        await set_honeypot(False, "user")
                    elif not settings.groq_api_key:
                        await send({"type": "error", "message": "honeypot needs GROQ_API_KEY"})
                    elif state.tracker.level() < 2:
                        await send({"type": "error", "message": "honeypot is only allowed once the call is L2 or higher"})
                    else:
                        await set_honeypot(True, "user")
    except WebSocketDisconnect:
        pass
    finally:
        if state.honeypot:
            await set_honeypot(False, "ended")
        await stream.close()
        if record and state.pcm:
            save_recording(call, bytes(state.pcm))


@app.post("/api/voices")
async def enrol_voice(audio: UploadFile = File(...), name: str = Form(...), consent: bool = Form(False)):
    try:
        return await enrol.enrol(await audio.read(), name, consent)
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


def _call_file(folder: str, call_id: str, ext: str) -> Path:
    if not call_id.replace("-", "").isalnum():
        raise HTTPException(400, "bad call id")
    return Path(settings.data_dir) / folder / f"{call_id}.{ext}"


@app.post("/api/report/{call_id}")
async def make_report(call_id: str):
    wav = _call_file("calls", call_id, "wav")
    if not wav.exists():
        raise HTTPException(404, "this call was not recorded (recording is opt-in)")
    try:
        await report.build(wav)
    except gnani.GnaniError as e:
        raise HTTPException(502, f"transcription failed: {e}")
    return {"url": f"/reports/{call_id}"}


@app.get("/reports/{call_id}")
def get_report(call_id: str):
    path = _call_file("reports", call_id, "html")
    if not path.exists():
        raise HTTPException(404, "no report yet")
    return FileResponse(path)


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
