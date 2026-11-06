"""Saavdhaan backend: serves the page and bridges browser mic audio to Gnani STT.

Browser --(16 kHz PCM over WS)--> backend --(headers + 1024-byte frames)--> Gnani
Browsers cannot set WebSocket headers, so the API key lives only here.
"""
import asyncio
import json
import wave
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from app import alerts, enrol, gnani
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


CALLS: dict[str, Call] = {}


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
async def guardian(ws: WebSocket, call: str, voice: str = "", record: int = 0):
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

    async def play_alert(level, lang):
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
            elif msg.get("text") and json.loads(msg["text"]).get("type") == "stop":
                break
    except WebSocketDisconnect:
        pass
    finally:
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


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
