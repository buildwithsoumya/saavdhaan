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
REFUSAL = re.compile(r"won.t tell|not telling|stop asking|none of your business|no more questions|मत पूछ|नहीं बताऊ|mat pooch|nahi bata", re.I)
THREAT = re.compile(r"arrest|jail|police.*(?:coming|door)|hurt|kill|गिरफ्तार|जेल|मार दू|giraftar", re.I)
SECRET = re.compile(r"don.t tell|stay alone|keep.*secret|किसी को मत|अकेले|kisi ko mat", re.I)
DEVICE = re.compile(r"install|download|anydesk|teamviewer|open.*(?:app|link)|इंस्टॉल|डाउनलोड|ऐप खोल", re.I)
PRIVATE = re.compile(r"\b(?:otp|pin|password|aadhaar|cvv|card number)\b|ओटीपी|पासवर्ड|आधार|सीवीवी", re.I)
URGENCY = re.compile(r"hurry|quick|immediately|right now|jaldi|abhi|जल्दी|अभी", re.I)


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
    if THREAT.search(caller):
        return TurnPlan("calm", None,
                        "Acknowledge the frightening claim in a short reply. Ask what the caller means; avoid unrelated evidence questions this turn.")
    if SECRET.search(caller):
        return TurnPlan("clarify", "department",
                        "Do not promise secrecy or isolation. Sound confused and ask why this must be private, without claiming anyone has left the room.")
    if REFUSAL.search(caller):
        return TurnPlan("deescalate", None,
                        "The caller refused a detail. Acknowledge briefly and ask them to explain their next instruction; do not interrogate them.")
    if PRIVATE.search(caller):
        repeats = sum(bool(PRIVATE.search(m.get("content", ""))) for m in history[:-1] if m.get("role") == "user")
        return TurnPlan("stall", None,
                        "Never provide or repeat private data. " +
                        ("Politely ask which office is calling." if not repeats else
                         "They have asked before: explain that the message is not visible, without asking them to resend or trigger another code."))
    if DEVICE.search(caller):
        attempts = sum(bool(DEVICE.search(m.get("content", ""))) for m in history[:-1] if m.get("role") == "user")
        obstacle = ("cannot find the app", "download is not starting", "phone storage is full")[attempts % 3]
        return TurnPlan("stall", "app_or_link", f"Use this device obstacle: {obstacle}. Ask for an explanation without opening, installing, or reading a code.")
    if URGENCY.search(caller):
        return TurnPlan("slow_down", None,
                        "Acknowledge the urgency, then ask for one instruction at a time. Do not claim to have acted or add a fresh identity question.")
    if "?" in caller or re.search(r"^(?:who|what|where|why|how|are you|can you|क्या|कौन|क्यों|kaun|kya)\b", caller, re.I):
        return TurnPlan("answer", None,
                        "Answer the caller's direct question first without inventing personal details, numbers, or completed actions. Do not switch to a checklist question.")
    priority = (["upi_or_account", "account_holder", "bank"] if PAYMENT_DEMAND.search(caller) else [])
    priority += list(FIELD_CUES)
    recent_questions = [m["content"] for m in history[:-1] if m.get("role") == "assistant"][-2:]
    cooling = set().union(*(question_targets(line) for line in recent_questions))
    target = next((field for field in priority if field not in evidence and field not in cooling), None)
    return TurnPlan("clarify" if target else "listen", target,
                    "Respond to the latest words, then ask one natural question about the target. Never ask for a known fact.")
