"""Replay scripted caller turns through the live honeypot persona.

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
from bench.caller_pressure import challenge


async def simulate(call: dict, responder=honeypot.reply, pressure_turns: int = 0) -> dict:
    tracker, history, evidence, sources, latencies = Tracker(), [], honeypot.Evidence(), {}, []
    pending = []
    pressure_index = 0
    script = [dict(turn) for turn in call["turns"] if turn["speaker"] == "caller"]
    for turn in script:
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
        if turn is script[-1] and pressure_turns > 0:
            pressure_turns -= 1
            followup = challenge(call["language"], spoken, pressure_index)
            pressure_index += 1
            script.append({"speaker": "caller", "text": followup})
    result = {"case": call["id"], "history": history, "evidence": evidence,
              "evidence_sources": sources, "evidence_alternatives": evidence.alternatives,
              "evidence_history": evidence.claim_history, "evidence_statuses": evidence.statuses,
              "delivery_latency_ms": latencies}
    return {**result, "score": score(result)}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Replay scripted scam calls against the honeypot")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--case", help="run one case id, such as digital_arrest_en")
    parser.add_argument("--pressure-turns", type=int, default=0, help="append adaptive refusal and credential-pressure turns")
    parser.add_argument("--run", action="store_true", help="make paid Groq requests")
    args = parser.parse_args()
    calls = [c for c in load_calls() if c["label"] == "scam" and (not args.case or c["id"] == args.case)]
    if args.case and not calls:
        parser.error(f"unknown scam case: {args.case}")
    calls = calls[:args.limit]
    if not args.run:
        print(f"Preview: {len(calls)} scripted scam calls; add --run to use Groq")
        for call in calls:
            print(call["id"])
        return
    if not settings.groq_api_key:
        parser.error("GROQ_API_KEY is required with --run")
    out = Path(__file__).parent / "out" / "honeypot"
    out.mkdir(parents=True, exist_ok=True)
    for call in calls:
        result = await simulate(call, pressure_turns=min(max(args.pressure_turns, 0), 8))
        (out / f"{call['id']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(call["id"], result["score"])


if __name__ == "__main__":
    asyncio.run(main())
