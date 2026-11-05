"""Family voice enrolment: consented reference audio -> cached speaker embedding.

Embeddings are stored only on this machine (`data/embeddings`, gitignored) and
can be deleted at any time, which withdraws the voice.
"""
import json
import re
import time
import uuid
from pathlib import Path

from app import gnani
from app.audio import to_pcm, to_wav
from app.config import settings

MIN_S, MAX_S = 5, 30  # reference length the voice-clone API accepts


class EnrolError(ValueError):
    pass


def _dir() -> Path:
    d = Path(settings.data_dir) / "embeddings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(voice_id: str) -> Path | None:
    return _dir() / f"{voice_id}.json" if re.fullmatch(r"[0-9a-f]{12}", voice_id or "") else None


async def enrol(audio: bytes, name: str, consent: bool) -> dict:
    if not consent:
        raise EnrolError("consent is required to clone a voice")
    name = name.strip()[:40]
    if not name:
        raise EnrolError("a name is required")
    try:
        wav = to_wav(audio)
    except Exception as e:
        raise EnrolError("could not read the audio file") from e
    seconds = len(to_pcm(wav)) / 32000
    if not MIN_S <= seconds <= MAX_S:
        raise EnrolError(f"recording must be {MIN_S}-{MAX_S} seconds, got {seconds:.1f}")
    embedding = await gnani.clone_embedding(wav, "reference.wav")
    record = {"id": uuid.uuid4().hex[:12], "name": name, "consent": True,
              "consented_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "embedding": embedding}
    _path(record["id"]).write_text(json.dumps(record), encoding="utf-8")
    return {"id": record["id"], "name": name}


def load(voice_id: str) -> dict | None:
    path = _path(voice_id)
    if not path or not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    return record if record.get("consent") else None


def voices() -> list[dict]:
    out = []
    for p in sorted(_dir().glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        if r.get("consent"):
            out.append({"id": r["id"], "name": r["name"]})
    return out


def delete(voice_id: str) -> bool:
    path = _path(voice_id)
    if path and path.exists():
        path.unlink()
        return True
    return False
