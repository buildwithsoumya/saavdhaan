"""Scam kill-chain tracker.

Every scam call walks the same chain: S1 authority, S2 fear, S3 isolation,
S4 urgency, S5 extraction. Each stage counts once, within a rolling window.
"""
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

DATA = Path(__file__).parent / "data"
STAGES = ("S1", "S2", "S3", "S4", "S5")
STAGE_NAMES = {"S1": "Authority", "S2": "Fear", "S3": "Isolation", "S4": "Urgency", "S5": "Extraction"}
LEVEL_NAMES = {0: "Safe", 1: "Caution", 2: "Saavdhaan", 3: "Stop"}
WINDOW_S = 600
NEGATION_SPAN = 6  # tokens either side of an S5 phrase searched for a negation
PUNCT_BREAK = "[.?!।॥;|\n]"  # not commas: "if anyone asks for an OTP, don't give it"


@dataclass
class Hit:
    stage: str
    phrase: str
    t: float
    text: str
    negated: bool = False


def _pattern(phrase: str) -> str:
    """Latin phrases match whole words; Indic phrases match as substrings (suffixes)."""
    p = re.escape(phrase.lower())
    return rf"(?<!\w){p}(?!\w)" if phrase.isascii() else p


class Lexicon:
    def __init__(self, path=DATA / "lexicons.yaml"):
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        blocks = [raw["shared"], *raw["languages"].values()]
        self.phrases = {s: sorted({p.lower() for b in blocks for p in b.get(s, [])}, key=len, reverse=True)
                        for s in STAGES}
        negs = {n.lower() for b in blocks for n in b.get("negations", [])}
        self.negations = {n for n in negs if not n.startswith("~")}
        self.negation_suffixes = tuple(n[1:] for n in negs if n.startswith("~"))
        breaks = sorted({w.lower() for b in blocks for w in b.get("clause_breaks", [])}, key=len, reverse=True)
        self.clause_break = re.compile("|".join([PUNCT_BREAK, *map(_pattern, breaks)]))
        self.regex = {s: re.compile("|".join(map(_pattern, ps))) for s, ps in self.phrases.items()}

    def is_negation(self, token: str) -> bool:
        token = token.strip(".,!?\"'()")
        return token in self.negations or (len(token) > 3 and token.endswith(self.negation_suffixes))

    def negated(self, text: str, start: int, end: int) -> bool:
        """Is there a negation near text[start:end], inside the same clause?"""
        left = max((m.end() for m in self.clause_break.finditer(text, 0, start)), default=0)
        nxt = self.clause_break.search(text, end)
        right = nxt.start() if nxt else len(text)
        window = text[left:start].split()[-NEGATION_SPAN:] + text[end:right].split()[:NEGATION_SPAN]
        return any(self.is_negation(tok) for tok in window)

    def match(self, text: str):
        """Yield (stage, phrase, negated) for every stage phrase in `text`."""
        low = text.lower()
        for stage, rx in self.regex.items():
            for m in rx.finditer(low):
                yield stage, m.group(0), stage == "S5" and self.negated(low, m.start(), m.end())


_default = None


def default_lexicon() -> Lexicon:
    global _default
    _default = _default or Lexicon()
    return _default


class Tracker:
    """Per-call state. Lives on the backend keyed by call id, so it survives
    both Gnani session rotation and browser reconnects."""

    def __init__(self, lexicon: Lexicon | None = None, window_s=WINDOW_S, clock=time.time):
        self.lex = lexicon or default_lexicon()
        self.window_s, self.clock = window_s, clock
        self.hits: list[Hit] = []
        self.ignored: list[Hit] = []  # negated S5 phrases, shown in the explanation
        self.followed = False

    def feed(self, text: str, t: float | None = None) -> list[Hit]:
        t = self.clock() if t is None else t
        new = []
        for stage, phrase, negated in self.lex.match(text):
            hit = Hit(stage, phrase, t, text, negated)
            (self.ignored if negated else self.hits).append(hit)
            if not negated:
                new.append(hit)
        return new

    def active(self, now: float | None = None) -> dict[str, list[Hit]]:
        now = self.clock() if now is None else now
        out: dict[str, list[Hit]] = {}
        for h in self.hits:
            if now - h.t <= self.window_s:
                out.setdefault(h.stage, []).append(h)
        return out

    def level(self, now: float | None = None) -> int:
        act = self.active(now)
        if "S5" in act:
            first_threat = min((h.t for s in ("S1", "S2") for h in act.get(s, [])), default=None)
            if first_threat is not None and max(h.t for h in act["S5"]) >= first_threat:
                return 3
        n = len(act)
        return 2 if n >= 3 else 1 if n == 2 else 0

    def explain(self, now: float | None = None) -> dict:
        act = self.active(now)
        return {
            "level": self.level(now),
            "level_name": LEVEL_NAMES[self.level(now)],
            "stages": {s: [asdict(h) for h in hs] for s, hs in sorted(act.items())},
            "ignored": [asdict(h) for h in self.ignored[-10:]],
        }

    def take_follow_signal(self, now: float | None = None) -> bool:
        """True once per call, when S1 and S2 are both active: time to bias STT to extraction terms."""
        act = self.active(now)
        if not self.followed and "S1" in act and "S2" in act:
            self.followed = True
            return True
        return False


def keyterms(kind="base") -> list[str]:
    return yaml.safe_load((DATA / "keyterms.yaml").read_text(encoding="utf-8"))[kind][:200]
