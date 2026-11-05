"""Render bench calls to 16 kHz WAV with Timbre v3.0 (two voices, short gaps).

Every turn is cached in bench/out/tts, so a call is paid for once.
Writes bench/out/clean/<id>.wav and <id>.json (turn start/end times in seconds).
"""
import hashlib
import json
import wave
from pathlib import Path

import yaml

from app import gnani
from app.audio import to_pcm

ROOT = Path(__file__).parent
OUT = ROOT / "out"
RATE = 16000
GAP_S, LEAD_S, TAIL_S = 0.5, 0.8, 1.5


def load_calls(limit: int | None = None) -> list[dict]:
    calls = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "calls").glob("*.yaml"))]
    if limit:  # keep scam/benign balanced when limiting
        scam = [c for c in calls if c["label"] == "scam"]
        benign = [c for c in calls if c["label"] == "benign"]
        calls = scam[:(limit + 1) // 2] + benign[:limit // 2]
    return calls


def _tts_path(text, voice, lang) -> Path:
    key = hashlib.sha1(f"timbre-v3.0|{voice}|{lang}|{text}".encode()).hexdigest()[:16]
    return OUT / "tts" / f"{key}.wav"


def uncached_chars(calls) -> int:
    return sum(len(t["text"]) for c in calls for t in c["turns"]
               if not _tts_path(t["text"], c["speakers"][t["speaker"]], c["language"]).exists())


def silence(seconds) -> bytes:
    return b"\0\0" * int(RATE * seconds)


async def render_call(call) -> Path:
    wav_path = OUT / "clean" / f"{call['id']}.wav"
    if wav_path.exists():
        return wav_path
    (OUT / "tts").mkdir(parents=True, exist_ok=True)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    pcm, timeline = bytearray(silence(LEAD_S)), []
    for turn in call["turns"]:
        voice = call["speakers"][turn["speaker"]]
        cache = _tts_path(turn["text"], voice, call["language"])
        if not cache.exists():
            cache.write_bytes(await gnani.tts(turn["text"], voice=voice, language=call["language"]))
        start = len(pcm) / 2 / RATE
        pcm += to_pcm(cache.read_bytes(), RATE)
        timeline.append({"start": round(start, 3), "end": round(len(pcm) / 2 / RATE, 3)})
        pcm += silence(GAP_S)
    pcm += silence(TAIL_S)
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(pcm))
    wav_path.with_suffix(".json").write_text(json.dumps(timeline))
    return wav_path


def timeline(call) -> list[dict]:
    return json.loads((OUT / "clean" / f"{call['id']}.json").read_text())
