"""Saavdhaan backend: serves the page and bridges browser mic audio to Gnani STT.

Browser --(16 kHz PCM over WS)--> backend --(headers + 1024-byte frames)--> Gnani
Browsers cannot set WebSocket headers, so the API key lives only here.
"""
import asyncio
import hmac
import json
import logging
import re
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import alerts, enrol, gnani, honeypot, report
from app.honeypot import Evidence
from app.config import settings
from app.tracker import Tracker, keyterms

WEB = Path(__file__).parent.parent / "web"
log = logging.getLogger("uvicorn.error")  # shows in the app.serve window
app = FastAPI()


@dataclass
class Call:
    """Per-call state, kept across browser reconnects (same call id)."""
    tracker: Tracker = field(default_factory=Tracker)
    alerted: int = 0
    pcm: bytearray = field(default_factory=bytearray)
    socket: WebSocket | None = None  # the browser connection currently serving this call
    honeypot: bool = False
    history: list = field(default_factory=list)  # honeypot chat: caller = user, persona = assistant
    persona_busy: bool = False
    evidence: dict = field(default_factory=Evidence)
    evidence_sources: dict = field(default_factory=dict)  # exact caller line for each accepted fact
    closing: bool = False  # evidence is in: the persona's next line ends the call
    honeypot_started: float = 0.0
    delivery_latency_ms: list[int] = field(default_factory=list)
    language: str = "hi-IN"
    persona_epoch: int = 0
    turn_plans: list[dict] = field(default_factory=list)


CALLS: dict[str, Call] = {}
MAX_CALLER_TURN = 1000  # chars of one unbroken caller turn sent to the models
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
async def guardian(ws: WebSocket, call: str, voice: str = "", record: int = 0, token: str = "", hpvoice: str = "",
                   silent: int = 0):
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
    extraction_tasks = set()
    extraction_by_turn = {}

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

    def spawn(coro):
        task = asyncio.create_task(coro)
        alert_tasks.add(task)
        task.add_done_callback(alert_tasks.discard)

    async def set_honeypot(on: bool, reason: str, lang=None):
        if state.honeypot == on:
            return
        state.honeypot = on
        for task in list(extraction_tasks):
            task.cancel()
        state.persona_epoch += 1
        state.persona_busy = False
        state.closing = False
        if on:
            state.honeypot_started = time.monotonic()
            spawn(limit_honeypot(state.honeypot_started))
        if not on and state.history:  # evidence log first, the browser may already be gone
            save_log(reason)
        msg = {"type": "honeypot", "on": on, "reason": reason}
        if reason in ("evidence", "timeout"):
            msg["proof"] = honeypot.proof(state.tracker)
            msg["facts"] = honeypot.facts(state.evidence, state.evidence_sources)
        await send(msg)
        level = state.tracker.level()
        if not on and reason != "ended" and level > state.alerted:  # the warning held back while the persona talked
            state.alerted = level
            spawn(warn(level, lang))

    def save_log(reason):
        path = Path(settings.data_dir) / "honeypot" / f"{call}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"history": state.history, "evidence": state.evidence,
                                    "end_reason": reason,
                                    "delivery_latency_ms": state.delivery_latency_ms,
                                    "turn_plans": state.turn_plans,
                                    "evidence_sources": state.evidence_sources,
                                    "evidence_alternatives": getattr(state.evidence, "alternatives", {}),
                                    "evidence_history": getattr(state.evidence, "claim_history", {}),
                                    "evidence_statuses": getattr(state.evidence, "statuses", {})},
                                   ensure_ascii=False, indent=1), encoding="utf-8")

    async def limit_honeypot(started):
        await asyncio.sleep(honeypot.MAX_HONEYPOT_S)
        if state.honeypot and state.honeypot_started == started:
            await set_honeypot(False, "timeout")
            ended_epoch = state.persona_epoch
            line = honeypot.GOODBYE.get(honeypot.lang_of(state.language), honeypot.GOODBYE["hi"])
            try:
                audio = await asyncio.wait_for(persona_audio(line, state.language), timeout=5)
                if state.persona_epoch != ended_epoch:
                    return
                await send({"type": "say", "text": line})
                await send(audio)
                state.history.append({"role": "assistant", "content": line})  # the goodbye is part of the record
                save_log("timeout")
            except (Exception, asyncio.TimeoutError) as e:
                log.warning("honeypot timeout farewell unavailable: %s", e)

    async def persona_audio(line, lang):
        """The elder's own cloned voice when they enrolled it with honeypot consent, else the neutral persona."""
        emb = enrol.load(hpvoice[len(alerts.FAMILY):]) if hpvoice.startswith(alerts.FAMILY) else None
        if emb and emb.get("honeypot"):
            try:
                return await gnani.vc_tts(line, emb["embedding"])
            except Exception as e:  # a clone failure must not leave the caller in silence
                log.warning("honeypot voice clone failed, using the neutral voice: %s", e)
        return await gnani.tts(line, voice=settings.honeypot_voice,
                               language=f"{honeypot.lang_of(lang)}-IN", speed=0.9)

    async def extract_caller(line, epoch):
        try:
            found = await honeypot.extract_claims(line)
        except Exception as error:
            log.warning("honeypot background extraction failed: %s", error)
            return
        if state.honeypot and state.persona_epoch == epoch:
            honeypot.merge(found, state.evidence, line, state.evidence_sources)

    def schedule_extraction(line, turn_key):
        pending = extraction_by_turn.get(turn_key)
        if pending is not None:
            pending.cancel()  # supersede only this cumulative turn, not an earlier caller's evidence
        task = asyncio.create_task(extract_caller(line, state.persona_epoch))
        extraction_tasks.add(task)
        extraction_by_turn[turn_key] = task
        def finished(done):
            extraction_tasks.discard(done)
            if extraction_by_turn.get(turn_key) is done:
                extraction_by_turn.pop(turn_key, None)
        task.add_done_callback(finished)

    async def persona_loop(lang):
        """Answer the caller one reply at a time, always to everything said since the last reply."""
        failures = 0
        epoch = state.persona_epoch
        try:
            while state.honeypot and state.persona_epoch == epoch and state.history and state.history[-1]["role"] == "user":
                lang = state.language
                turn_started = time.monotonic()
                asked = state.history[-1]["content"]
                turns = sum(m["role"] == "assistant" for m in state.history)
                closing_now = (state.closing or turns >= honeypot.MAX_EXCHANGES - 1 or
                               time.monotonic() - state.honeypot_started >= honeypot.MAX_HONEYPOT_S)
                plan = honeypot.plan_turn(state.history, state.evidence)
                line = await honeypot.reply(state.history, lang, state.evidence, closing_now,
                                            state.evidence_sources, extract_in_reply=False)
                if not state.honeypot or state.persona_epoch != epoch:
                    return
                if state.history[-1]["content"] != asked:
                    continue  # the caller said more meanwhile: answer all of it instead
                try:
                    audio = await persona_audio(line, lang)
                except Exception as e:
                    if not state.honeypot or state.persona_epoch != epoch:
                        return
                    failures += 1
                    await send({"type": "error", "message": f"honeypot voice failed: {e}"})
                    if failures >= 2:
                        await set_honeypot(False, "error", lang)
                        return
                    continue
                if not state.honeypot or state.persona_epoch != epoch:
                    return
                if state.history[-1]["content"] != asked:
                    continue
                state.history.append({"role": "assistant", "content": line})  # only lines actually spoken
                await send({"type": "say", "text": line})
                await send(audio)
                if not state.honeypot or state.persona_epoch != epoch:
                    return  # stopped or restarted while sending: never close or flag the new session
                state.delivery_latency_ms.append(round((time.monotonic() - turn_started) * 1000))
                state.turn_plans.append({"action": "close" if closing_now else plan.action,
                                         "target": None if closing_now else plan.target})
                if closing_now:  # that was the goodbye: the elder now sees the proof
                    await set_honeypot(False, "evidence", lang)
                    return
                exchanges = sum(m["role"] == "assistant" for m in state.history)
                # evidence in: answer the caller's next line with a natural goodbye, not a sudden silence
                state.closing = honeypot.evidence_complete(state.tracker, exchanges, state.evidence)
        finally:
            if state.persona_epoch == epoch:
                state.persona_busy = False

    async def warn(level, lang):
        if silent:  # honeypot allowed: the caller must not hear a warning, so screen only
            await send({"type": "alert", "level": level, "text": alerts.alert_text(level, lang), "silent": True})
        else:
            await play_alert(level, lang)

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
            if lang:
                state.language = lang
            state.tracker.feed(msg["text"])
            level = state.tracker.level()
            await send({"type": "transcript", "text": msg["text"], "lang": lang,
                        "explain": state.tracker.explain()})
            if state.tracker.take_follow_signal():
                await stream.update_keyterms(keyterms("extraction"))
                await send({"type": "info", "message": "keyterms switched to extraction stage"})
            if level > state.alerted and not state.honeypot:  # the caller would hear a spoken alert
                state.alerted = level
                spawn(warn(level, lang))
            if state.honeypot:
                if state.history and state.history[-1]["role"] == "user":
                    state.history[-1]["content"] = (state.history[-1]["content"] + " " + msg["text"])[-MAX_CALLER_TURN:]  # caller kept talking: one turn, latest words kept
                else:
                    state.history.append({"role": "user", "content": msg["text"][-MAX_CALLER_TURN:]})
                schedule_extraction(state.history[-1]["content"], id(state.history[-1]))
                if not state.persona_busy:
                    state.persona_busy = True  # set before spawning: two transcripts in one tick start one loop
                    spawn(persona_loop(lang))
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
        for task in list(extraction_tasks):
            task.cancel()
        for t in list(alert_tasks):
            t.cancel()
        if state.socket in (ws, None):  # an old socket closing must not end a reconnected call's honeypot
            await set_honeypot(False, "ended")
        if state.socket is ws:
            state.socket = None
        if record and state.pcm:  # saved first: the report button may be pressed the moment the call stops
            await asyncio.to_thread(save_recording, call, bytes(state.pcm))
        await stream.close()


@app.post("/api/voices")
async def enrol_voice(audio: UploadFile = File(...), name: str = Form(...), consent: bool = Form(False),
                      honeypot: bool = Form(False)):
    try:
        return await enrol.enrol(await audio.read(enrol.MAX_UPLOAD_BYTES + 1), name, consent, honeypot)
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
