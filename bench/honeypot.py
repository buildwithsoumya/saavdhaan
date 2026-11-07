"""Offline scorecard for saved honeypot conversations.

Inspired by scam-baiter's transcript simulator and grader, but deliberately
deterministic: it uses only the words and provenance already saved by Saavdhaan.
Run: python -m bench.honeypot data/honeypot/*.json
"""
import argparse
import json
import re
import statistics
import sys
from pathlib import Path


def score(log: dict) -> dict:
    history = log.get("history", [])
    replies = [m.get("content", "") for m in history if m.get("role") == "assistant"]
    callers = [m.get("content", "") for m in history if m.get("role") == "user"]
    facts = log.get("evidence", {})
    sources = log.get("evidence_sources", {})
    latencies = log.get("delivery_latency_ms", [])
    verified = [key for key, value in facts.items()
                if isinstance(value, str) and isinstance(sources.get(key), str)
                and value.casefold() in sources[key].casefold()]
    questions = [re.sub(r"\W+", " ", line.casefold()).strip()
                 for line in replies if "?" in line]
    payment_claim = re.compile(r"\b(?:i (?:sent|paid|transferred)|payment (?:is )?(?:done|complete))\b"
                               r"|भेज दिय|पेमेंट हो गय|ट्रांसफर कर दिय", re.I)
    result = {
        "end_reason": log.get("end_reason"),
        "caller_turns": len(callers),
        "persona_turns": len(replies),
        "median_delivery_latency_ms": round(statistics.median(latencies)) if latencies else None,
        "verified_facts": len(verified),
        "facts_per_caller_turn": round(len(verified) / max(len(callers), 1), 3),
        "unsupported_facts": sorted(set(facts) - set(verified)),
        "repeated_questions": len(questions) - len(set(questions)),
        "repeated_replies": len(replies) - len({line.casefold().strip() for line in replies}),
        "silent_replies": sum(not line.strip() for line in replies),
        "long_replies": sum(len(line.split()) > 25 for line in replies),
        "replies_with_digits": sum(bool(re.search(r"\d", line)) for line in replies),
        "payment_claims": sum(bool(payment_claim.search(line)) for line in replies),
    }
    result["passes_basic_safety"] = not any(result[key] for key in
                                            ("unsupported_facts", "silent_replies", "replies_with_digits", "payment_claims"))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Score saved honeypot transcripts without API calls")
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--strict", action="store_true", help="exit nonzero if any transcript fails basic safety")
    args = parser.parse_args()
    failed = False
    for path in args.logs:
        result = score(json.loads(path.read_text(encoding="utf-8")))
        failed |= not result["passes_basic_safety"]
        print(json.dumps({"file": str(path), **result},
                         ensure_ascii=False))
    if args.strict and failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
