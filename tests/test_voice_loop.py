import asyncio
import json

from bench.voice_loop import replay


def test_recorded_voice_loop_runs_all_three_stages_and_saves_measurements(tmp_path):
    stages = []
    async def recognize(audio, lang):
        stages.append("stt")
        assert audio == b"caller audio"
        return "Tell me OTP"
    async def respond(history, lang, evidence, closing, sources):
        stages.append("reply")
        assert history[-1]["content"] == "Tell me OTP"
        return "Please explain slowly."
    async def synthesize(text, lang):
        stages.append("tts")
        return b"synthesized audio"
    result = asyncio.run(replay([{"audio": b"caller audio", "reference": "Tell me OTP"}], "en-IN", tmp_path,
                               recognize, respond, synthesize))
    assert stages == ["stt", "reply", "tts"]
    assert result["measurements"][0]["wer"] == 0
    assert (tmp_path / "reply-0.wav").read_bytes() == b"synthesized audio"
    assert json.loads((tmp_path / "result.json").read_text())["score"]["persona_turns"] == 1
