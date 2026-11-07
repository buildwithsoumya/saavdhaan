import asyncio

from bench.honeypot_sim import simulate
from bench.render import load_calls


def test_synthetic_caller_replay_uses_live_reply_interface():
    seen = []
    async def responder(history, language, evidence, closing, sources):
        seen.append((history[-1]["content"], language, closing))
        return "Please explain that again?"

    case = next(c for c in load_calls() if c["id"] == "digital_arrest_en")
    result = asyncio.run(simulate(case, responder))
    assert seen and all(lang == "en-IN" for _, lang, _ in seen)
    assert result["score"]["persona_turns"] == len(seen)
    assert result["history"][0]["role"] == "user"
