"""Replay synthetic caller turns through the live honeypot persona.

The caller side is fixed, so the run is reproducible. Groq is used only when
--run is supplied; otherwise this command previews the selected cases.
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

from app import honeypot
from app.config import settings
from app.tracker import Tracker
from bench.honeypot import score
from bench.render import load_calls


async def simulate(call: dict, responder=honeypot.reply) -> dict:
    tracker, history, evidence, sources, latencies = Tracker(), [], {}, {}, []
    pending = []
    for turn in call["turns"]:
        if turn["speaker"] != "caller":
            continue
        line = turn["text"]
        tracker.feed(line)
        pending.append(line)
        if tracker.level() < 2:
            continue
        history.append({"role": "user", "content": " ".join(pending)})
        pending.clear()
        count = sum(m["role"] == "assistant" for m in history)
        closing = honeypot.evidence_complete(tracker, count, evidence) or count >= honeypot.MAX_EXCHANGES - 1
        started = time.perf_counter()
        spoken = await responder(history, call["language"], evidence, closing, sources)
        latencies.append(round((time.perf_counter() - started) * 1000))
        history.append({"role": "assistant", "content": spoken})
        if closing:
            break
    result = {"case": call["id"], "history": history, "evidence": evidence,
              "evidence_sources": sources, "delivery_latency_ms": latencies}
    return {**result, "score": score(result)}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Replay synthetic scam scripts against the honeypot")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--run", action="store_true", help="make paid Groq requests")
    args = parser.parse_args()
    calls = [c for c in load_calls() if c["label"] == "scam"][:args.limit]
    if not args.run:
        print(f"Preview: {len(calls)} synthetic scam calls; add --run to use Groq")
        for call in calls:
            print(call["id"])
        return
    if not settings.groq_api_key:
        parser.error("GROQ_API_KEY is required with --run")
    out = Path(__file__).parent / "out" / "honeypot"
    out.mkdir(parents=True, exist_ok=True)
    for call in calls:
        result = await simulate(call)
        (out / f"{call['id']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(call["id"], result["score"])


if __name__ == "__main__":
    asyncio.run(main())
