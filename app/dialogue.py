"""Deterministic conversation planning around the language model.

Plans choose a goal, never invent an answer or treat caller claims as verified identity.
"""
import re
import json
from difflib import SequenceMatcher
from dataclasses import dataclass
from app.language_rules import has_private_term

FIELD_CUES = {
    "name": r"\bname\b|नाम|naam",
    "department": r"\bdepartment\b|\bcompany\b|विभाग|कंपनी",
    "badge_or_case": r"\bbadge\b|\bcase (?:number|id)\b|केस|बैज",
    "office": r"\boffice\b|\bstation\b|\bbranch\b|थाना|दफ्तर|शाखा",
    "bank": r"\bbank\b|बैंक",
    "account_holder": r"\bwhose\b|\bholder\b|किसके|खाताधारक",
    "upi_or_account": r"\bupi\b|\baccount\b|खाता|यूपीआई",
    "app_or_link": r"\bapps?\b|\blink\b|\bwebsite\b|ऐप|लिंक",
}
PAYMENT_DEMAND = re.compile(r"\btransfer|\bpayment\b|\bsend money\b|\bupi\b|पैसे|भेजो|ट्रांसफर|\bbhejo\b|\bpaisa\b", re.I)
REFUSAL = re.compile(r"won.t tell|not telling|stop asking|none of your business|no more questions|मत पूछ|नहीं बताऊ|mat pooch|nahi bata", re.I)
THREAT = re.compile(r"\barrest|\bjail\b|\bpolice\b.*\b(?:coming|door)\b|\bhurt\b|\bkill\b|गिरफ्तार|जेल|मार दू|\bgiraftar", re.I)
SECRET = re.compile(r"don.t tell|stay alone|keep.*secret|किसी को मत|अकेले|kisi ko mat", re.I)
DEVICE = re.compile(r"\binstall|\bdownload|\banydesk\b|\bteamviewer\b|\bopen\b.*\b(?:apps?|link)\b|इंस्टॉल|डाउनलोड|ऐप खोल", re.I)
PRIVATE = re.compile(r"\b(?:otp|pin|password|aadhaar|cvv|card number)\b|ओटीपी|पासवर्ड|आधार|सीवीवी", re.I)
URGENCY = re.compile(r"\bhurry\b|\bquick(?:ly)?\b|\bimmediately\b|\bright now\b|\bjaldi\b|\babhi\b|जल्दी|अभी", re.I)


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


def memory_notes(history: list[dict]) -> str:
    spoken = [m.get("content", "") for m in history if m.get("role") == "assistant"]
    return json.dumps({"first_spoken_replies": [line[:220] for line in spoken[:3]],
                       "questions_already_tried": sorted(set().union(*(question_targets(line) for line in spoken)))},
                      ensure_ascii=False)


def repeats_reply(line: str, history: list[dict]) -> bool:
    normalize = lambda text: " ".join(re.findall(r"\w+", text.casefold()))
    candidate = normalize(line)
    return any(candidate == normalize(m.get("content", "")) or
               (len(candidate.split()) >= 4 and SequenceMatcher(None, candidate, normalize(m.get("content", ""))).ratio() > .88)
               for m in history[-8:] if m.get("role") == "assistant")


def plan_turn(history: list[dict], evidence: dict) -> TurnPlan:
    caller = history[-1].get("content", "") if history and history[-1].get("role") == "user" else ""
    # Calm/answer/listen turns ask for no evidence. Scam scripts repeat "arrest" or "jaldi" every turn, so unless
    # the last line asked for a field, this one must pursue evidence (still answering first). Safety stalls are exempt.
    last = next((m.get("content", "") for m in reversed(history[:-1]) if m.get("role") == "assistant"), None)
    soft = last is None or bool(question_targets(last))
    if soft and THREAT.search(caller):
        return TurnPlan("calm", None,
                        "Acknowledge the frightening claim in a short reply. Ask what the caller means; avoid unrelated evidence questions this turn.")
    if SECRET.search(caller):
        return TurnPlan("clarify", "department",
                        "Do not promise secrecy or isolation. Sound confused and ask why this must be private, without claiming anyone has left the room.")
    if soft and REFUSAL.search(caller):
        return TurnPlan("deescalate", None,
                        "The caller refused a detail. Acknowledge briefly and ask them to explain their next instruction; do not interrogate them.")
    if has_private_term(caller) or PRIVATE.search(caller):
        repeats = sum(bool(has_private_term(m.get("content", "")) or PRIVATE.search(m.get("content", ""))) for m in history[:-1] if m.get("role") == "user")
        return TurnPlan("stall", None,
                        "Never provide or repeat private data. " +
                        ("Politely ask which office is calling." if not repeats else
                         "They have asked before: explain that the message is not visible, without asking them to resend or trigger another code."))
    if DEVICE.search(caller):
        attempts = sum(bool(DEVICE.search(m.get("content", ""))) for m in history[:-1] if m.get("role") == "user")
        obstacle = ("cannot find the app", "download is not starting", "phone storage is full")[attempts % 3]
        return TurnPlan("stall", "app_or_link", f"Use this device obstacle: {obstacle}. Ask for an explanation without opening, installing, or reading a code.")
    if soft and URGENCY.search(caller):
        return TurnPlan("slow_down", None,
                        "Acknowledge the urgency, then ask for one instruction at a time. Do not claim to have acted or add a fresh identity question.")
    if soft and caller.strip().casefold().rstrip(".!?") in ("hello", "yes", "okay", "ok", "hmm", "haan", "जी", "हाँ", "हां"):
        return TurnPlan("listen", None,
                        "This is only a short acknowledgement. Briefly acknowledge it and invite the caller to continue; infer no new facts.")
    if soft and ("?" in caller or re.search(r"^(?:who|what|where|why|how|are you|can you|क्या|कौन|क्यों|kaun|kya)\b", caller, re.I)):
        return TurnPlan("answer", None,
                        "Answer the caller's direct question first without inventing personal details, numbers, or completed actions. Do not switch to a checklist question.")
    priority = (["upi_or_account", "account_holder", "bank"] if PAYMENT_DEMAND.search(caller) else [])
    priority += list(FIELD_CUES)
    recent_questions = [m["content"] for m in history[:-1] if m.get("role") == "assistant"][-2:]
    cooling = set().union(*(question_targets(line) for line in recent_questions))
    attempts = {field: sum(field in question_targets(m.get("content", "")) for m in history
                           if m.get("role") == "assistant") for field in FIELD_CUES}
    conflicts = getattr(evidence, "alternatives", {})
    field = next((key for key in conflicts if key not in cooling and attempts.get(key, 0) < 2), None)
    if field:
        return TurnPlan("resolve_conflict", field,
                        "The caller gave different claims for this field. Ask which claim they mean without reading identifiers or numbers back. Do not choose one yourself.")
    target = next((field for field in priority if field not in evidence and field not in cooling
                   and attempts[field] < 2), None)
    return TurnPlan("clarify" if target else "listen", target,
                    "Respond to the latest words first (answer, calm or reassure them briefly if needed), then ask one "
                    "natural question about the target. Never ask for a known fact.")
