"""Post-call report: batch STT with speaker separation -> one-page HTML with the
stage timeline, who said what, and a draft complaint for 1930 / Chakshu.

    python -m app.report path/to/call.wav     # writes data/reports/<name>.html
"""
import asyncio
import html
import sys
import time
from pathlib import Path

from app import gnani
from app.audio import ffmpeg
from app.config import settings
from app.tracker import LEVEL_NAMES, STAGE_NAMES, Tracker

PLACEHOLDERS = ["[Your full name]", "[Your phone number]", "[Date and time of the call]",
                "[Number the call came from, as shown on your phone]",
                "[Amount lost, if any, and transaction reference]"]


def mmss(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def analyse(doc: dict) -> dict:
    """Run the tracker over diarized segments; the speaker who trips the most stages is the suspected caller."""
    tracker, per_speaker, lines = Tracker(window_s=10 ** 9), {}, []
    for seg in doc.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        t, spk = float(seg.get("start_time") or 0), seg.get("speaker_id")
        hits = tracker.feed(text, t)
        per_speaker[spk] = per_speaker.get(spk, 0) + len(hits)
        lines.append({"t": float(seg.get("start_time") or 0), "speaker": spk, "text": text,
                      "stages": sorted({h.stage for h in hits})})
    caller = max(per_speaker, key=per_speaker.get) if per_speaker and max(per_speaker.values()) else None
    first = {}
    for h in tracker.hits:
        first.setdefault(h.stage, h)
    return {"lines": lines, "caller": caller, "first": first, "level": tracker.level(10 ** 9), "tracker": tracker}


def complaint(a: dict) -> str:
    phrases = {s: sorted({h.phrase for h in a["tracker"].hits if h.stage == s}) for s in STAGE_NAMES}
    what = {"S1": "claimed authority", "S2": "threatened me", "S3": "told me to keep it secret or stay on the line",
            "S4": "pressed for urgency", "S5": "asked for money or details"}
    heard = [f"{what[s]} ({', '.join(f'“{p}”' for p in phrases[s][:4])})" for s in STAGE_NAMES if phrases[s]]
    behaviour = ("The caller " + "; ".join(heard) + ".") if heard else \
        "[Describe what the caller said and asked for.]"
    return "\n".join([
        "To: National Cyber Crime Reporting Portal (helpline 1930) / Chakshu, Sanchar Saathi",
        "",
        f"I, {PLACEHOLDERS[0]}, contact number {PLACEHOLDERS[1]}, report a suspected fraud call received on "
        f"{PLACEHOLDERS[2]} from {PLACEHOLDERS[3]}.",
        "",
        behaviour,
        "",
        f"Loss: {PLACEHOLDERS[4]}.",
        "A transcript with timestamps is attached.",
    ])


CSS = """
:root{--paper:#d9e7ef;--ink:#15233f;--soft:#3a4d6b;--rule:#9db8cb;--stop:#8d0c0c;color-scheme:light}
body{margin:0;background:var(--paper);color:var(--ink);font:17px/1.5 "Nirmala UI","Noto Sans",system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 48px}
h1{margin:0;font-size:1.8rem}h2{margin:28px 0 10px;font-size:1.2rem}
.verdict{display:inline-block;margin:10px 0;padding:6px 14px;border:4px double var(--stop);border-radius:12px;color:var(--stop);font-weight:800;font-size:1.3rem}
ol.timeline{padding-left:0;list-style:none}ol.timeline li{padding:6px 0;border-bottom:1px solid var(--rule)}
.t{font-variant-numeric:tabular-nums;color:var(--soft);margin-right:10px}
.who{font-weight:700;margin-right:8px}.tag{margin-left:8px;padding:0 6px;border:1px solid var(--stop);border-radius:6px;color:var(--stop);font-size:.85rem;font-weight:700}
pre{white-space:pre-wrap;padding:14px;border:2px dashed var(--soft);border-radius:6px;background:#f4f8fb;font:inherit}
.note{color:var(--soft);font-size:.95rem}
@media print{body{background:#fff}}
"""


def render_html(a: dict, title: str) -> str:
    who = lambda spk: "Caller (suspected)" if spk == a["caller"] else f"Speaker {html.escape(str(spk))}"
    stages = "".join(
        f'<li><span class="t">{mmss(h.t)}</span><span class="who">{s} {STAGE_NAMES[s]}</span>'
        f'“{html.escape(h.phrase)}” <span class="note">in: {html.escape(h.text[:120])}</span></li>'
        for s, h in sorted(a["first"].items(), key=lambda kv: kv[1].t))
    lines = "".join(
        f'<li><span class="t">{mmss(l["t"])}</span><span class="who">{who(l["speaker"])}:</span>'
        f'{html.escape(l["text"])}{"".join(f"<span class=tag>{s}</span>" for s in l["stages"])}</li>'
        for l in a["lines"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Call report</title><style>{CSS}</style></head>
<body><main>
<h1>सावधान · Saavdhaan call report</h1>
<p class="note">{html.escape(title)} · generated {time.strftime("%Y-%m-%d %H:%M")}</p>
<p class="verdict">L{a["level"]} · {LEVEL_NAMES[a["level"]]}</p>
<h2>Scam stages, in the order they appeared</h2>
<ol class="timeline">{stages or "<li>No scam stages detected.</li>"}</ol>
<h2>Who said what</h2>
<ol class="timeline">{lines or "<li>No speech found.</li>"}</ol>
<h2>Draft complaint (fill in the brackets)</h2>
<pre>{html.escape(complaint(a))}</pre>
<p class="note">Call 1930 or file at cybercrime.gov.in. Report the number at Chakshu (sancharsaathi.gov.in).
Speaker labels come from automatic speaker separation and may be wrong.</p>
</main></body></html>"""


def to_mp3(audio: bytes) -> bytes:
    """Speech MP3 (~0.25 MB/min) keeps hour-long calls under the 10 MB direct-upload limit."""
    return ffmpeg(["-ac", "1", "-ar", "16000", "-b:a", "32k", "-f", "mp3"], audio)


async def build(audio_path: Path) -> Path:
    # ffmpeg and file I/O run in threads so a live call on the same server never stalls
    mp3 = await asyncio.to_thread(lambda: to_mp3(audio_path.read_bytes()))
    doc = await gnani.batch_transcribe(mp3, audio_path.stem + ".mp3")
    out = Path(settings.data_dir) / "reports" / f"{audio_path.stem}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(out.write_text, render_html(analyse(doc), audio_path.name), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(asyncio.run(build(Path(sys.argv[1]))))
