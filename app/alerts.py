"""Spoken alerts: text per level and language, synthesized once and cached on disk."""
import hashlib
import re
from pathlib import Path

import yaml

from app import enrol, gnani
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


FAMILY = "family:"  # voice value prefix for an enrolled family voice


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>\s*", "", text).strip()


async def _cache(key: str, make) -> bytes:
    path = _cached(key)
    if not path.exists():
        path.write_bytes(await make())
    return path.read_bytes()


async def alert_audio(level: int, code: str | None, voice: str | None = None) -> bytes:
    """WAV for an alert. Alert lines never change, so each (text, voice) pair is
    paid for once and then served from disk.

    With a family voice chosen, the L2 line is spoken in that cloned voice
    (tags stripped: `vachana-vc-v1` does not document audio tags). L1 and L3
    rely on tags (<slow>, <shouting>) so they stay on Timbre v3.0, and any
    voice-clone failure falls back to Timbre too."""
    voice = voice or settings.alert_voice
    text = f"{TAGS[level]} {alert_text(level, code)}"
    if level == 3:
        text = text.replace("! ", "! <pauses> ", 1)
    if voice.startswith(FAMILY):
        emb = enrol.load(voice[len(FAMILY):])
        if level == 2 and emb:
            plain = strip_tags(text)
            try:
                return await _cache(f"vc|{emb['id']}|{plain}", lambda: gnani.vc_tts(plain, emb["embedding"]))
            except gnani.GnaniError:
                pass
        voice = settings.alert_voice
    return await _cache(f"timbre-v3.0|{voice}|{text}",
                        lambda: gnani.tts(text, voice=voice, language=f"{lang_of(code)}-IN"))
