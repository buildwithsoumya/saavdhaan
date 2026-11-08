"""Run the scam tracker over INDICA's text (10 languages, scam vs legit) without downloading the audio.

    curl -L -o data/indica/Text_samples.tar.gz https://huggingface.co/datasets/vikrant-vikram/INDICA/resolve/main/Text_samples.tar.gz
    python -m bench.indica                       # all languages
    python -m bench.indica --langs hindi english

INDICA is TeleAntiFraud (Chinese) machine-translated into Indic languages: its scams are shopping, loan
and app-download pitches, not the Indian impersonation chain Saavdhaan targets. Read "legit flagged" as the
false-alarm rate on everyday calls; low "scam flagged" is expected and marks scam types outside our scope.
The dataset card states no licence: use it for private evaluation only and do not redistribute it.
"""
import argparse
import collections
import re
import sys
import tarfile
from pathlib import Path

from app.tracker import Tracker

DEFAULT = Path("data/indica/Text_samples.tar.gz")


def level(text: str) -> int:
    """Highest alert level over the whole conversation, one transcript per line."""
    tr = Tracker(window_s=10 ** 9)  # a recorded conversation has no live clock
    for i, line in enumerate(text.splitlines()):
        tr.feed(re.sub(r"^Speaker\d+:\s*", "", line), float(i))
    return tr.level(10 ** 8)


def evaluate(archive: Path, langs: set[str] | None = None) -> dict:
    """{language: {label: [conversations, L1+ (caution shown), L2+ (warning)]}} from Text_samples/Text_<lang>/<label>/*.txt."""
    out = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0]))
    with tarfile.open(archive) as t:
        for m in t:
            parts = m.name.split("/")
            if not m.isfile() or len(parts) != 4:
                continue
            lang, label = parts[1].removeprefix("Text_"), parts[2]
            if langs and lang not in langs:
                continue
            row = out[lang][label]
            lvl = level(t.extractfile(m).read().decode("utf-8", "replace"))
            row[0] += 1
            row[1] += lvl >= 1
            row[2] += lvl >= 2
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("archive", nargs="?", type=Path, default=DEFAULT)
    p.add_argument("--langs", nargs="+", help="e.g. hindi english tamil")
    a = p.parse_args()
    if not a.archive.exists():
        sys.exit(f"{a.archive} not found; download it first (see python -m bench.indica --help)")
    sys.stdout.reconfigure(encoding="utf-8")
    # the live app shows Caution at L1 and speaks its warning from L2; report both
    print(f"{'language':10} {'legit L1+':>16} {'legit L2+':>16} {'scam L1+':>16} {'scam L2+':>16}")
    for lang, rows in sorted(evaluate(a.archive, set(a.langs or [])).items()):
        cells = []
        for label in ("legit", "scam"):
            n, l1, l2 = rows.get(label, [0, 0, 0])
            cells += [f"{hit}/{n} ({100 * hit / n:.1f}%)" if n else "-" for hit in (l1, l2)]
        print(f"{lang:10} " + " ".join(f"{c:>16}" for c in cells))


if __name__ == "__main__":
    main()
