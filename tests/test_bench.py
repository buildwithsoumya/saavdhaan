import asyncio
import csv
import subprocess

from bench import degrade, render, report, run


def test_wer():
    assert run.wer("main CBI se bol raha hoon", "main CBI se bol raha hoon") == 0
    assert run.wer("a b c d", "a x c") == 0.5  # one substitution, one deletion
    assert run.wer("Hello, OTP!", "hello otp") == 0  # case and punctuation are normalised
    assert run.wer("आपका खाता", "आपका") == 0.5
    assert run.wer("", "extra words") == 2


def test_keyterm_recall():
    assert run.keyterm_recall("send the OTP by UPI", "send the otp by you pee") == 0.5
    assert run.keyterm_recall("hello there", "hello") is None


def test_score_pre_payment_timing():
    call = {"id": "x", "label": "scam", "template": "t", "variant": "en", "s5_turn": 2,
            "turns": [{"text": "CBI here"}, {"text": "arrest warrant, urgent"}, {"text": "transfer now"}]}
    tl = [{"start": 0, "end": 2}, {"start": 3, "end": 6}, {"start": 7, "end": 9}]
    row = run.score(call, tl, [(2.5, "CBI here"), (6.6, "arrest warrant, urgent"), (9.7, "transfer now")])
    assert row["t_l2"] == 6.6 and row["t_l3"] == 9.7 and row["s5_end"] == 9 and row["wer"] == 0


def test_degrade_command_building():
    args = degrade.phone_args("in.wav", "out.wav")
    assert args[args.index("-af") + 1] == "highpass=f=300,lowpass=f=3400"
    assert args[args.index("-ar") + 1] == "8000" and args[args.index("-c:a") + 1] == "pcm_mulaw"
    mix = degrade.noise_mix_args("in.wav", "out.wav", 12.5, -7.25)
    assert "anoisesrc=color=pink" in mix[mix.index("lavfi") + 2] and "duration=12.50" in mix[mix.index("lavfi") + 2]
    assert "[1]volume=-7.25dB[n]" in mix[mix.index("-filter_complex") + 1]
    wins = degrade.dropout_windows(30, seed=1)
    assert wins == degrade.dropout_windows(30, seed=1)  # reproducible
    assert all(0.019 <= b - a <= 0.061 for a, b in wins)  # 20-60 ms, ms-rounded and 8 <= len(wins) <= 30
    af = degrade.dropout_args("p.wav", "d.wav", [(1.0, 1.03), (2.5, 2.54)])
    assert af[af.index("-af") + 1] == "volume=0:enable='between(t,1.0,1.03)+between(t,2.5,2.54)'"
    assert degrade.rate_of("clean") == 16000 and degrade.rate_of("phone_snr5") == 8000


def test_degrade_runs_ffmpeg(tmp_path):
    clean = tmp_path / "clean" / "c.wav"
    clean.parent.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=3:sample_rate=16000",
                    str(clean)], check=True)
    for cond in degrade.CONDITIONS:
        out = degrade.degrade(clean, cond, seed=3)
        assert out.exists() and abs(degrade.duration_s(out) - 3) < 0.1, cond


def test_render_caches_turns(monkeypatch, tmp_path):
    monkeypatch.setattr(render, "OUT", tmp_path)
    calls = []

    async def fake_tts(text, voice, language):
        calls.append(text)
        return subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", "-f", "wav", "-"],
                              capture_output=True, check=True).stdout

    monkeypatch.setattr(render.gnani, "tts", fake_tts)
    call = {"id": "c1", "language": "hi-IN", "speakers": {"caller": "Avinash", "victim": "Lakshmi"},
            "turns": [{"speaker": "caller", "text": "a"}, {"speaker": "victim", "text": "b"},
                      {"speaker": "caller", "text": "a"}]}
    assert render.uncached_chars([call]) == 3
    asyncio.run(render.render_call(call))
    assert calls == ["a", "b"]  # the repeated line is synthesized once
    tl = render.timeline(call)
    assert tl[0]["start"] == render.LEAD_S and abs(tl[0]["end"] - tl[0]["start"] - 1) < 0.05
    assert render.uncached_chars([call]) == 0


def test_dataset_is_balanced_and_annotated():
    calls = render.load_calls()
    scams = [c for c in calls if c["label"] == "scam"]
    assert len(scams) == 30 and len(calls) == 60
    assert {c["variant"] for c in calls} == {"hi", "cm", "en", "ta", "bn"}
    for c in scams:
        assert 0 <= c["s5_turn"] < len(c["turns"])
    assert all(c["needs_native_review"] for c in calls if c["variant"] in ("ta", "bn"))
    assert len(render.load_calls(4)) == 4


def test_text_baseline_and_report(tmp_path):
    rows = [run.text_baseline(c) for c in render.load_calls()]
    path = tmp_path / "results.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, run.FIELDS)
        w.writeheader()
        w.writerows(rows)
    (g,) = report.aggregate(list(csv.DictReader(path.open(encoding="utf-8"))))
    assert g["recall_l2"] >= 0.9 and g["fa_l2"] <= 0.05 and g["threshold"] in (1, 2)
    assert "SaavdhaanBench" in report.make(path).read_text(encoding="utf-8")
