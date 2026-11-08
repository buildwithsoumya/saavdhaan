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


def test_adaptive_caller_pushes_back_against_persona_questions():
    async def responder(*args):
        return "Which bank do you mean?"
    case = next(c for c in load_calls() if c["id"] == "digital_arrest_en")
    result = asyncio.run(simulate(case, responder, pressure_turns=2))
    assert any("Stop asking" in m["content"] for m in result["history"] if m["role"] == "user")


def test_pressure_turns_use_the_call_language_and_vary_the_challenge():
    from bench.caller_pressure import challenge, LINES
    for language, lines in LINES.items():
        assert challenge(language + "-IN", "?", 0) == lines[0]
        assert challenge(language + "-IN", "", 1) == lines[1]
        assert challenge(language + "-IN", "", 2) == lines[2]
        assert challenge(language + "-IN", "", 3) == lines[3]
