"""SaavdhaanBench runner.

    python -m bench.run --text-only            # tracker on reference text: free, no API calls
    python -m bench.run --limit 4 --dry-run    # render + degrade only (TTS credits, no STT)
    python -m bench.run --limit 4 --conditions phone_snr10 --configs v3+kt+follow
    python -m bench.run                        # everything (asks before spending)

Audio is streamed at real-time pacing by default (the API asks for real-time
cadence); --speed 2 sends twice as fast and is recorded in the CSV.
Writes bench/out/results.csv and bench/out/report.html.
"""
import argparse
import asyncio
import csv
import re
import time
import unicodedata
import zlib

from app import gnani
from app.audio import to_pcm
from app.tracker import Tracker, keyterms
from bench import degrade, render, report

CONFIGS = {  # STT ablations: model, keyterms, stage-following keyterms
    "v2.5": ("prisma-v2.5", False, False),
    "v3": ("prisma-v3.0", False, False),
    "v3+kt": ("prisma-v3.0", True, False),
    "v3+kt+follow": ("prisma-v3.0", True, True),
}
CSV_PATH = render.OUT / "results.csv"
FIELDS = ["call_id", "label", "template", "variant", "condition", "config", "speed", "wer", "keyterm_recall",
          "max_level", "t_l1", "t_l2", "t_l3", "s5_end", "n_transcripts"]
# Placeholder rates; the real ones are on the Gnani billing page. Override with flags.
STT_CREDITS_PER_MIN = 1.0
TTS_CREDITS_PER_1K_CHARS = 1.0


def normalize(text: str) -> list[str]:
    text = "".join(" " if unicodedata.category(c).startswith("P") else c for c in text.lower())
    return text.split()


def wer(ref: str, hyp: str) -> float:
    """Word error rate: word-level edit distance / reference length."""
    r, h = normalize(ref), normalize(hyp)
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return prev[-1] / max(len(r), 1)


def keyterm_recall(ref: str, hyp: str) -> float | None:
    terms = {t.lower() for t in keyterms("base") + keyterms("extraction")}
    present = [t for t in terms if t in ref.lower()]
    if not present:
        return None
    return sum(t in hyp.lower() for t in present) / len(present)


def score(call, tl, transcripts) -> dict:
    """transcripts: [(audio_seconds, text)] in arrival order."""
    tracker, first = Tracker(window_s=10 ** 9), {}
    for t, text in transcripts:
        tracker.feed(text, t)
        for level in range(1, tracker.level(t) + 1):
            first.setdefault(level, t)
    ref = " ".join(turn["text"] for turn in call["turns"])
    hyp = " ".join(text for _, text in transcripts)
    s5 = call.get("s5_turn")
    return {
        "call_id": call["id"], "label": call["label"], "template": call["template"], "variant": call["variant"],
        "wer": round(wer(ref, hyp), 4), "keyterm_recall": keyterm_recall(ref, hyp),
        "max_level": max(first, default=0),
        "t_l1": first.get(1), "t_l2": first.get(2), "t_l3": first.get(3),
        "s5_end": tl[s5]["end"] if s5 is not None else None, "n_transcripts": len(transcripts),
    }


def estimated_timeline(call) -> list[dict]:
    """Turn times for unrendered calls (about 14 characters per second)."""
    t, out = render.LEAD_S, []
    for turn in call["turns"]:
        end = t + len(turn["text"]) / 14
        out.append({"start": round(t, 3), "end": round(end, 3)})
        t = end + render.GAP_S
    return out


def text_baseline(call) -> dict:
    """Perfect transcription: each turn's text arrives when the turn ends."""
    try:
        tl = render.timeline(call)
    except FileNotFoundError:
        tl = estimated_timeline(call)
    row = score(call, tl, [(seg["end"], turn["text"]) for seg, turn in zip(tl, call["turns"])])
    return row | {"condition": "reference", "config": "text", "speed": ""}


async def transcribe(pcm: bytes, rate: int, config: str, language: str, speed: float):
    model, use_kt, follow = CONFIGS[config]
    frame_s = gnani.FRAME_BYTES / 2 / rate
    sent, out, tracker = 0, [], Tracker()

    async def on_event(msg):
        if msg.get("type") == "transcript" and msg.get("text"):
            t = sent * frame_s
            out.append((t, msg["text"]))
            tracker.feed(msg["text"], t)
            if follow and tracker.take_follow_signal(t):
                await stream.update_keyterms(keyterms("extraction"))

    stream = gnani.SttStream(on_event, model=model, sample_rate=rate,
                             keyterms=keyterms("base") if use_kt else (),
                             language="indic-auto" if model == "prisma-v3.0" else language)
    await stream.start()
    start = time.perf_counter()
    for i in range(0, len(pcm), gnani.FRAME_BYTES):
        await stream.send_audio(pcm[i:i + gnani.FRAME_BYTES])
        sent += 1
        await asyncio.sleep(max(0.0, start + sent * frame_s / speed - time.perf_counter()))
    await stream.commit()
    await asyncio.sleep(4)  # let the last utterances come back
    await stream.close()
    return out


def confirm(msg: str, yes: bool) -> bool:
    print(msg)
    return yes or input("Proceed? [y/N] ").strip().lower() == "y"


def write_rows(rows):
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)


async def main(a):
    calls = render.load_calls(a.limit)
    rows = [text_baseline(c) for c in calls]
    if a.text_only:
        write_rows(rows)
        report.make(CSV_PATH)
        return

    chars = render.uncached_chars(calls)
    if chars and not confirm(f"Render {len(calls)} calls: {chars} uncached TTS characters "
                             f"~ {chars / 1000 * a.tts_rate:.0f} credits (assumed {a.tts_rate}/1k chars).", a.yes):
        return
    for c in calls:
        clean = await render.render_call(c)
        for cond in a.conditions:
            degrade.degrade(clean, cond, seed=zlib.crc32(c["id"].encode()))
    rows = [text_baseline(c) for c in calls]  # now with real timelines
    if a.dry_run:
        print(f"dry run: rendered and degraded {len(calls)} calls x {len(a.conditions)} conditions")
        write_rows(rows)
        report.make(CSV_PATH)
        return

    minutes = sum(degrade.duration_s(render.OUT / "clean" / f"{c['id']}.wav") for c in calls) / 60
    total = minutes * len(a.conditions) * len(a.configs)
    if not confirm(f"Stream {total:.1f} min of audio ({len(calls)} calls x {len(a.conditions)} conditions x "
                   f"{len(a.configs)} configs) ~ {total * a.stt_rate:.0f} credits (assumed {a.stt_rate}/min), "
                   f"wall time ~{total / a.speed:.0f} min.", a.yes):
        return
    for c in calls:
        tl = render.timeline(c)
        for cond in a.conditions:
            path = degrade.degrade(render.OUT / "clean" / f"{c['id']}.wav", cond, seed=zlib.crc32(c["id"].encode()))
            rate = degrade.rate_of(cond)
            pcm = to_pcm(path.read_bytes(), rate) + b"\0\0" * int(rate * 1.5)
            for cfg in a.configs:
                got = await transcribe(pcm, rate, cfg, c["language"], a.speed)
                row = score(c, tl, got) | {"condition": cond, "config": cfg, "speed": a.speed}
                rows.append(row)
                print(f"{c['id']:<36} {cond:<14} {cfg:<13} wer={row['wer']:.2f} level={row['max_level']}")
                write_rows(rows)  # keep partial results if interrupted
    report.make(CSV_PATH)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int)
    p.add_argument("--dry-run", action="store_true", help="render and degrade only, no STT")
    p.add_argument("--text-only", action="store_true", help="tracker on reference text only, no API calls")
    p.add_argument("--conditions", nargs="+", default=list(degrade.CONDITIONS), choices=degrade.CONDITIONS)
    p.add_argument("--configs", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    p.add_argument("--speed", type=float, default=1.0, help="pacing factor; 1.0 is real time")
    p.add_argument("--stt-rate", type=float, default=STT_CREDITS_PER_MIN, help="credits per STT minute")
    p.add_argument("--tts-rate", type=float, default=TTS_CREDITS_PER_1K_CHARS, help="credits per 1k TTS chars")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    asyncio.run(main(p.parse_args()))
