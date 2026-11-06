"""Spoken alerts: text per level and language, synthesized once and cached on disk."""
import hashlib
from pathlib import Path

import yaml

from app import gnani
from app.config import settings
from app.tracker import DATA

TAGS = {1: "<slow>", 2: "<whispers>", 3: "<shouting>"}  # Timbre v3.0 audio tags
MESSAGES = yaml.safe_load((DATA / "alerts.yaml").read_text(encoding="utf-8"))
FALLBACK_LANG = "hi"  # gu, ml, pa have no alert text yet


def lang_of(code: str | None) -> str:
    lang = (code or "").split("-")[0]
    return lang if lang in MESSAGES else FALLBACK_LANG


def alert_text(level: int, code: str | None) -> str:
    m = MESSAGES[lang_of(code)]
    return m[f"L{level}"].format(helpline=m["helpline"])


def _cached(key: str) -> Path:
    d = Path(settings.data_dir) / "alerts"
    d.mkdir(parents=True, exist_ok=True)
    return d / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".wav")


async def alert_audio(level: int, code: str | None, voice: str | None = None) -> bytes:
    """WAV for an alert in Timbre v3.0. Alert lines never change, so each
    (text, voice) pair is paid for once and then served from disk."""
    voice = voice or settings.alert_voice
    text = f"{TAGS[level]} {alert_text(level, code)}"
    if level == 3:
        text = text.replace("! ", "! <pauses> ", 1)
    path = _cached(f"timbre-v3.0|{voice}|{text}")
    if not path.exists():
        audio = await gnani.tts(text, voice=voice, language=f"{lang_of(code)}-IN")
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(audio)
        tmp.replace(path)  # never leave a half-written file in the cache
    return path.read_bytes()
