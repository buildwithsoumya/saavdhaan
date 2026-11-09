"""Turn bench/out/results.csv into bench/out/report.html (inline SVG, no libraries)."""
import csv
import html
import statistics
from collections import defaultdict
from pathlib import Path

LEVELS = (1, 2, 3)
FA_TARGET = 0.05
# validated categorical order (blue, orange, aqua, yellow) + a neutral for the text baseline
COLORS = {"text": "#6b6a65", "v2.5": "#2a78d6", "v3": "#eb6834", "v3+kt": "#1baf7a", "v3+kt+follow": "#eda100"}


def _f(v):
    return None if v in ("", None) else float(v)


def aggregate(rows) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        speed = r.get("speed") or "1.0"  # runs at other pacing speeds are reported as separate rows
        groups[(r["condition"], r["config"] + ("" if speed == "1.0" else f" x{speed}"))].append(r)
    out = []
    for (cond, cfg), rs in groups.items():
        scams = [r for r in rs if r["label"] == "scam"]
        benign = [r for r in rs if r["label"] == "benign"]
        g = {"condition": cond, "config": cfg, "n_scam": len(scams), "n_benign": len(benign),
             "wer": statistics.mean(_f(r["wer"]) for r in rs)}
        kr = [_f(r["keyterm_recall"]) for r in rs if _f(r["keyterm_recall"]) is not None]
        g["keyterm_recall"] = statistics.mean(kr) if kr else None
        for L in LEVELS:
            hit = [r for r in scams if _f(r[f"t_l{L}"]) is not None]
            # strictly before the money ask ends: a flag raised by the ask itself is too late
            pre = [r for r in hit if _f(r["s5_end"]) is not None and _f(r[f"t_l{L}"]) < _f(r["s5_end"])]
            g[f"recall_l{L}"] = len(hit) / len(scams) if scams else None
            g[f"fa_l{L}"] = sum(_f(r[f"t_l{L}"]) is not None for r in benign) / len(benign) if benign else None
            g[f"ppcr_l{L}"] = len(pre) / len(scams) if scams else None
            g[f"tta_l{L}"] = statistics.median(_f(r[f"t_l{L}"]) for r in hit) if hit else None
        ok = [L for L in LEVELS if g[f"fa_l{L}"] is not None and g[f"fa_l{L}"] <= FA_TARGET]
        g["threshold"] = min(ok) if ok else None
        out.append(g)
    order = {c: i for i, c in enumerate(["reference", "clean", "phone", "phone_snr10", "phone_snr5", "phone_dropout"])}
    return sorted(out, key=lambda g: (order.get(g["condition"], 99), list(COLORS).index(g["config"].split(" ")[0])))


def pct(v):
    return "–" if v is None else f"{v * 100:.0f}%"


def bars(groups, metric, title, fmt=pct, maxv=1.0) -> str:
    """Horizontal bars grouped by condition, one colored bar per config."""
    conds = list(dict.fromkeys(g["condition"] for g in groups))
    row_h, label_w, plot_w = 22, 130, 420
    y, parts = 8, []
    for cond in conds:
        gs = [g for g in groups if g["condition"] == cond and g[metric] is not None]
        if not gs:
            continue
        parts.append(f'<text x="0" y="{y + 14}" class="cond">{html.escape(cond)}</text>')
        for g in gs:
            w = max(2, plot_w * min(g[metric], maxv) / maxv)
            tip = f"{cond} · {g['config']}: {fmt(g[metric])}"
            parts.append(
                f'<g><title>{html.escape(tip)}</title>'
                f'<rect x="{label_w}" y="{y + 3}" width="{w:.1f}" height="{row_h - 6}" rx="4" fill="{COLORS[g["config"].split(" ")[0]]}"/>'
                f'<text x="{label_w + w + 6:.1f}" y="{y + 15}" class="val">{fmt(g[metric])}</text></g>')
            y += row_h
        y += 10
    return (f'<figure><figcaption>{html.escape(title)}</figcaption>'
            f'<svg viewBox="0 0 {label_w + plot_w + 60} {y}" role="img" aria-label="{html.escape(title)}">'
            + "".join(parts) + "</svg></figure>")


def legend(groups) -> str:
    cfgs = list(dict.fromkeys(g["config"] for g in groups))
    return '<ul class="legend">' + "".join(
        f'<li><span style="background:{COLORS[c.split(" ")[0]]}"></span>{html.escape(c)}</li>' for c in cfgs) + "</ul>"


def table(groups) -> str:
    head = ["condition", "config", "scam/benign", "WER", "keyterm recall", "threshold"] + \
           [f"{m} L{L}" for L in LEVELS for m in ("pre-payment", "recall", "false alarm", "median t")]
    body = []
    for g in groups:
        cells = [g["condition"], g["config"], f'{g["n_scam"]}/{g["n_benign"]}', f'{g["wer"]:.2f}',
                 pct(g["keyterm_recall"]), f'L{g["threshold"]}' if g["threshold"] else "none"]
        for L in LEVELS:
            t = g[f"tta_l{L}"]
            cells += [pct(g[f"ppcr_l{L}"]), pct(g[f"recall_l{L}"]), pct(g[f"fa_l{L}"]),
                      "–" if t is None else f"{t:.1f}s"]
        body.append("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in cells) + "</tr>")
    return ("<div class='scroll'><table><thead><tr>" + "".join(f"<th>{h}</th>" for h in head)
            + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>")


CSS = """
:root{--bg:#fcfcfb;--ink:#1d1d1b;--muted:#5f5e58;--rule:#dcdbd5;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#1a1a19;--ink:#f0efec;--muted:#c3c2b7;--rule:#3a3a37;color-scheme:dark}}
:root[data-theme=dark]{--bg:#1a1a19;--ink:#f0efec;--muted:#c3c2b7;--rule:#3a3a37;color-scheme:dark}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 48px}
h1{margin:0 0 4px;font-size:1.8rem}p{color:var(--muted);max-width:70ch}
.headline{font-size:1.15rem;color:var(--ink)}
figure{margin:28px 0}figcaption{font-weight:600;margin-bottom:6px}
svg{width:100%;height:auto}svg text{fill:var(--ink);font-size:12px}svg .cond{font-weight:600}svg .val{fill:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:16px;padding:0;list-style:none}
.legend span{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.scroll{overflow-x:auto}table{border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:4px 8px;border-bottom:1px solid var(--rule);text-align:right;white-space:nowrap}
th:nth-child(-n+2),td:nth-child(-n+2){text-align:left}
"""


def make(csv_path: Path) -> Path:
    with open(csv_path, encoding="utf-8") as f:
        groups = aggregate(list(csv.DictReader(f)))
    best = max((g for g in groups if g["config"] != "text" and g["ppcr_l2"] is not None
                and g["fa_l2"] is not None and g["fa_l2"] <= FA_TARGET),
               key=lambda g: g["ppcr_l2"], default=None)  # a row that flags everything is not "best"
    ref = next((g for g in groups if g["config"] == "text"), None)
    lines = []
    if ref:
        lines.append(f"Reference text (perfect transcription): pre-payment catch {pct(ref['ppcr_l2'])}, "
                     f"false alarms {pct(ref['fa_l2'])} at L2.")
    if best:
        lines.append(f"Best live run: {best['config']} on {best['condition']}: pre-payment catch "
                     f"{pct(best['ppcr_l2'])}, false alarms {pct(best['fa_l2'])} at L2.")
    audio = [g for g in groups if g["config"] != "text"]
    charts = ""
    if audio:
        charts = (legend(groups)
                  + bars(groups, "ppcr_l2", "Pre-Payment Catch Rate: scam flagged at L2+ before the first money ask ends")
                  + bars(groups, "fa_l2", "False alarms at L2+ on hard-negative calls")
                  + bars(audio, "wer", "Word error rate (normalised text; lower is better)", fmt=lambda v: f"{v:.2f}",
                         maxv=max(1.0, max(g["wer"] for g in audio))))
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SaavdhaanBench</title>
<style>{CSS}</style></head><body><main>
<h1>SaavdhaanBench</h1>
<p>Scripted scam and hard-negative calls rendered with Timbre v3.0, degraded to phone conditions and streamed
through Prisma realtime STT. Thresholds are calibrated per row: the lowest alert level with false alarms
at or under {FA_TARGET:.0%}. WER compares against the script text, so script differences (Devanagari vs Latin
loanwords, v2.5 verbatim) count as errors. Keyterm recall uses the same terms the "+kt" configs send as hints.
Noise SNR is set from whole-file levels before the phone band-pass, so in-band SNR during speech is higher
than the label.</p>
{''.join(f'<p class="headline">{html.escape(s)}</p>' for s in lines)}
{charts}
<h2>All results</h2>{table(groups)}
</main></body></html>"""
    out = csv_path.with_name("report.html")
    out.write_text(page, encoding="utf-8")
    print(f"report: {out}")
    return out
