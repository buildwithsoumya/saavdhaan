"""Replay local caller WAV turns through STT -> persona -> TTS.

Manifest: {"id":"synthetic", "language":"en-IN", "turns":[{"audio":"caller.wav", "reference":"..."}]}.
Paths are relative to the manifest. Preview is free; --run uses speech and Groq credits.
This measures service latency, not speakerphone acoustics or actual caller retention.
"""
import argparse
import asyncio
import json
import time
from pathlib import Path
from dataclasses import dataclass

from app import gnani, honeypot
from app.audio import to_pcm
from app.config import settings
from bench.run import transcribe, wer
from bench.honeypot import score


@dataclass(frozen=True)
class Recognition:
    text: str
    input_duration_ms: int | None = None
    collection_grace_ms: int | None = None


async def recognize(audio: bytes, language: str) -> Recognition:
    pcm = await asyncio.to_thread(to_pcm, audio)
    segments = await transcribe(pcm, 16000, "v3+kt+follow", language, 1.0)
    # bench.run.transcribe paces the input at real time and then waits four seconds.
    return Recognition(" ".join(text for _, text in segments), round(len(pcm) / 32), 4000)


async def synthesize(text: str, language: str) -> bytes:
    return await gnani.tts(text, voice=settings.honeypot_voice, language=language, speed=.9)


async def replay(turns, language, output: Path, recognizer=recognize, responder=honeypot.reply, speaker=synthesize):
    history, evidence, sources, measurements = [], honeypot.Evidence(), {}, []
    output.mkdir(parents=True, exist_ok=True)
    for index, turn in enumerate(turns[:honeypot.MAX_EXCHANGES]):
        started = time.perf_counter()
        recognized = await recognizer(turn["audio"], language)
        recognition = recognized if isinstance(recognized, Recognition) else Recognition(recognized)
        text = recognition.text
        heard = time.perf_counter()
        if not text.strip():
            measurements.append({"turn": index, "empty_transcript": True})
            continue
        history.append({"role": "user", "content": text})
        spoken = await responder(history, language, evidence, index == honeypot.MAX_EXCHANGES - 1, sources)
        generated = time.perf_counter()
        audio = await speaker(spoken, language)
        finished = time.perf_counter()
        path = output / f"reply-{index}.wav"
        path.write_bytes(audio)
        history.append({"role": "assistant", "content": spoken})
        row = {"turn": index, "stt_replay_ms": round((heard-started)*1000),
               "input_duration_ms": recognition.input_duration_ms,
               "collection_grace_ms": recognition.collection_grace_ms,
               "generation_ms": round((generated-heard)*1000), "tts_ms": round((finished-generated)*1000),
               "reply_pipeline_ms": round((finished-heard)*1000),
               "replay_loop_ms": round((finished-started)*1000), "reply_audio": str(path)}
        if "reference" in turn:
            row["wer"] = wer(turn["reference"], text)
        measurements.append(row)
    result = {"history": history, "evidence": evidence, "evidence_sources": sources,
              "evidence_history": evidence.claim_history, "measurements": measurements}
    result["score"] = score(result)
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("bench/out/voice-loop"))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    paths = [(args.manifest.parent / turn["audio"]).resolve() for turn in manifest["turns"]]
    if not all(path.is_file() for path in paths):
        parser.error("a caller audio file is missing")
    if not args.run:
        print(f"Preview: {len(paths)} local audio turns in {manifest['language']}; --run uses paid STT, Groq and TTS")
        return
    if not settings.gnani_api_key or not settings.groq_api_key:
        parser.error("GNANI_API_KEY and GROQ_API_KEY are required with --run")
    turns = [{**turn, "audio": path.read_bytes()} for turn, path in zip(manifest["turns"], paths)]
    result = await replay(turns, manifest["language"], args.output)
    print(json.dumps(result["score"], ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
