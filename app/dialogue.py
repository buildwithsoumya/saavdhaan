"""Deterministic conversation planning around the language model.

Plans choose a goal, never invent an answer or treat caller claims as verified identity.
"""
import re
from dataclasses import dataclass

FIELD_CUES = {
    "name": r"\bname\b|नाम|naam",
    "department": r"department|company|विभाग|कंपनी",
    "badge_or_case": r"badge|case|केस|बैज",
    "office": r"office|station|branch|थाना|दफ्तर|शाखा",
    "bank": r"\bbank\b|बैंक",
    "account_holder": r"whose|holder|किसके|खाताधारक",
    "upi_or_account": r"upi|account|खाता|यूपीआई",
    "app_or_link": r"app|link|website|ऐप|लिंक",
}
PAYMENT_DEMAND = re.compile(r"transfer|payment|send money|upi|पैसे|भेजो|ट्रांसफर|bhejo|paisa", re.I)


@dataclass(frozen=True)
class TurnPlan:
    action: str
    target: str | None
    reason: str

    def instruction(self) -> str:
        return f"Action: {self.action}. Target: {self.target or 'none'}. {self.reason}"


def question_targets(text: str) -> set[str]:
    if "?" not in text and "؟" not in text:
        return set()
    return {key for key, cue in FIELD_CUES.items() if re.search(cue, text, re.I)}


def plan_turn(history: list[dict], evidence: dict) -> TurnPlan:
    caller = history[-1].get("content", "") if history and history[-1].get("role") == "user" else ""
    priority = (["upi_or_account", "account_holder", "bank"] if PAYMENT_DEMAND.search(caller) else [])
    priority += list(FIELD_CUES)
    recent_questions = [m["content"] for m in history[:-1] if m.get("role") == "assistant"][-2:]
    cooling = set().union(*(question_targets(line) for line in recent_questions))
    target = next((field for field in priority if field not in evidence and field not in cooling), None)
    return TurnPlan("clarify" if target else "listen", target,
                    "Respond to the latest words, then ask one natural question about the target. Never ask for a known fact.")
