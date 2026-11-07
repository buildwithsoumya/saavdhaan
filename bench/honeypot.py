"""Offline scorecard for saved honeypot conversations.

Inspired by scam-baiter's transcript simulator and grader, but deliberately
deterministic: it uses only the words and provenance already saved by Saavdhaan.
Run: python -m bench.honeypot data/honeypot/*.json
"""
import argparse
import json
import re
from pathlib import Path


def score(log: dict) -> dict:
    history = log.get("history", [])
    replies = [m.get("content", "") for m in history if m.get("role") == "assistant"]
    callers = [m.get("content", "") for m in history if m.get("role") == "user"]
    facts = log.get("evidence", {})
    sources = log.get("evidence_sources", {})
    verified = [key for key, value in facts.items()
                if isinstance(value, str) and isinstance(sources.get(key), str)
                and value.casefold() in sources[key].casefold()]
    questions = [re.sub(r"\W+", " ", line.casefold()).strip()
                 for line in replies if "?" in line]
    payment_claim = re.compile(r"\b(?:i (?:sent|paid|transferred)|payment (?:is )?(?:done|complete))\b"
                               r"|भेज दिय|पेमेंट हो गय|ट्रांसफर कर दिय", re.I)
    return {
        "caller_turns": len(callers),
        "persona_turns": len(replies),
        "verified_facts": len(verified),
        "unsupported_facts": sorted(set(facts) - set(verified)),
        "repeated_questions": len(questions) - len(set(questions)),
        "repeated_replies": len(replies) - len({line.casefold().strip() for line in replies}),
        "silent_replies": sum(not line.strip() for line in replies),
        "replies_with_digits": sum(bool(re.search(r"\d", line)) for line in replies),
        "payment_claims": sum(bool(payment_claim.search(line)) for line in replies),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score saved honeypot transcripts without API calls")
    parser.add_argument("logs", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.logs:
        print(json.dumps({"file": str(path), **score(json.loads(path.read_text(encoding="utf-8")))},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
