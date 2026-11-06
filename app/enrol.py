"""Family voice enrolment: consented reference audio -> cached speaker embedding.

Embeddings are stored only on this machine (`data/embeddings`, gitignored) and
can be deleted at any time, which withdraws the voice and its cached audio.
"""
import asyncio
import json
import re
import shutil
import time
import uuid
from pathlib import Path

from app import gnani
from app.audio import to_pcm, to_wav
from app.config import settings

MIN_S, MAX_S = 5, 30  # reference length the voice-clone API accepts
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class EnrolError(ValueError):
    pass


def _dir() -> Path:
    d = Path(settings.data_dir) / "embeddings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(voice_id: str) -> Path | None:
    return _dir() / f"{voice_id}.json" if re.fullmatch(r"[0-9a-f]{12}", voice_id or "") else None


def cache_dir(voice_id: str) -> Path:
    """Where alert lines spoken in this voice are cached; removed with the voice."""
    return Path(settings.data_dir) / "alerts" / "vc" / voice_id


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # a half-written or corrupt file counts as absent


async def enrol(audio: bytes, name: str, consent: bool, honeypot: bool = False) -> dict:
    """`honeypot`: the speaker also agrees that this voice may talk to scam callers in
    honeypot mode. Only the owner of the voice should give that; it is stored with the voice."""
    if not consent:
        raise EnrolError("consent is required to clone a voice")
    name = name.strip()[:40]
    if not name:
        raise EnrolError("a name is required")
    if len(audio) > MAX_UPLOAD_BYTES:
        raise EnrolError("the recording file is too large (max 5 MB)")
    try:  # ffmpeg runs off the event loop so a live call is never stalled
        wav = await asyncio.to_thread(to_wav, audio, 16000, MAX_S + 1)
        seconds = len(await asyncio.to_thread(to_pcm, wav)) / 32000
    except Exception as e:
        raise EnrolError("could not read the audio file") from e
    if not MIN_S <= seconds <= MAX_S:
        raise EnrolError(f"recording must be {MIN_S}-{MAX_S} seconds, got {seconds:.1f}")
    embedding = await gnani.clone_embedding(wav, "reference.wav")
    record = {"id": uuid.uuid4().hex[:12], "name": name, "consent": True, "honeypot": bool(honeypot),
              "consented_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "embedding": embedding}
    _path(record["id"]).write_text(json.dumps(record), encoding="utf-8")
    return {"id": record["id"], "name": name, "honeypot": record["honeypot"]}


def load(voice_id: str) -> dict | None:
    path = _path(voice_id)
    record = _read(path) if path and path.exists() else None
    return record if record and record.get("consent") else None


def voices() -> list[dict]:
    records = (_read(p) for p in sorted(_dir().glob("*.json")))
    return [{"id": r["id"], "name": r["name"], "honeypot": bool(r.get("honeypot"))}
            for r in records if r and r.get("consent")]


def delete(voice_id: str) -> bool:
    path = _path(voice_id)
    if not path or not path.exists():
        return False
    path.unlink()
    shutil.rmtree(cache_dir(voice_id), ignore_errors=True)
    return True
