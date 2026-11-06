"""Honeypot mode: once a call is clearly a scam (L2+), Saavdhaan can take over the
talking so the elder can step away, keeping the caller busy while the transcript
collects evidence. It speaks in the elder's own cloned voice when the elder enrolled
it with honeypot consent, otherwise in a neutral persona voice. When enough evidence
is in, the elder is shown the caller's own words for each scam stage as proof.

Hard rules, enforced in code and not only in the prompt:
- never any digits, so no OTP, account, Aadhaar or card number, real or fake
- never agree that money was sent
- replies are short, plain speech, no tags
"""
import logging
import re

import httpx

from app.config import settings
from app.tracker import STAGE_NAMES, Tracker

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
log = logging.getLogger("uvicorn.error")  # shows in the app.serve window
MIN_EXCHANGES = 4  # persona turns before "enough evidence" can be declared
transport: httpx.AsyncBaseTransport | None = None  # tests swap in httpx.MockTransport

LANG_NAMES = {"hi": "Hindi", "en": "English", "ta": "Tamil", "bn": "Bengali", "kn": "Kannada", "te": "Telugu",
              "mr": "Marathi", "gu": "Gujarati", "ml": "Malayalam", "pa": "Punjabi"}

SYSTEM = """You are the elderly person who received this phone call, speaking in your own voice. You are slow,
polite, worried and a little confused. The caller is a scammer, but they must never suspect that you know:
sound scared and willing to cooperate, and believe what they say.

Your goal is to keep them talking and make them reveal, one detail at a time, evidence for a police complaint:
their full name, the department or company they claim, their badge, ID or case number, the police station or
office, the reason they give, exactly where they want money sent (bank name, account holder, UPI ID, app),
and any links or apps they push.

How to talk:
- Answer what the caller just said, then ask for one new detail you do not have yet. Never ask for a detail
  they already gave; use it back to them ("Inspector Verma ji, which station did you say?").
- Never repeat a sentence you already said. Move the conversation forward every turn.
- Bait them: say you are ready to do what they ask and only need one more detail first (whose name the account
  is in, which branch, how to write their UPI ID, what to tell the bank if it asks).
- When they push you to hurry, stay calm and stall with a new everyday reason each time (the bank app is
  loading, the passbook is in the other room, the phone network is weak), then ask your next question.

Rules you must never break:
- Never say any number or digit. If asked for an OTP, account, Aadhaar, PIN, card or any number, stall:
  your glasses are missing, the phone is slow, you are looking for the passbook, ask them to repeat,
  or ask for their own name and ID first.
- Never say you have sent, transferred or paid money. Say you are trying, or the bank app is not opening.
- Never install anything; say the phone is old and the app is not downloading.
- Ask one simple question back in most replies, to make them explain more.
- Reply in {language}, in plain spoken words only, one or two short sentences (under 25 words).
- No stage directions, brackets, emojis or tags. Never mention being a program or a recording."""

STALL = {
    "hi": "एक मिनट बेटा, मेरा चश्मा नहीं मिल रहा। आप अपना नाम फिर से बताइए?",
    "en": "One minute, I can't find my glasses. What did you say your name was?",
    "ta": "ஒரு நிமிடம், என் கண்ணாடி கிடைக்கவில்லை. உங்கள் பெயர் என்ன சொன்னீர்கள்?",
    "bn": "এক মিনিট, চশমাটা খুঁজে পাচ্ছি না। আপনার নামটা আবার বলবেন?",
    "kn": "ಒಂದು ನಿಮಿಷ, ನನ್ನ ಕನ್ನಡಕ ಸಿಗುತ್ತಿಲ್ಲ. ನಿಮ್ಮ ಹೆಸರು ಮತ್ತೆ ಹೇಳಿ?",
    "te": "ఒక్క నిమిషం, నా కళ్ళద్దాలు కనిపించడం లేదు. మీ పేరు మళ్ళీ చెప్పండి?",
    "mr": "एक मिनिट, माझा चष्मा सापडत नाही. तुमचं नाव पुन्हा सांगा?",
}

DIGITS = re.compile(r"\d")  # \d covers every script's digits
NUMBER_WORDS = [  # spelled-out digits: two or more in a row could read out an OTP
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "shunya", "ek", "do", "teen", "char", "chaar", "paanch", "panch", "chhe", "che", "saat", "aath", "nau",
    "शून्य", "एक", "दो", "तीन", "चार", "पांच", "पाँच", "छह", "छः", "सात", "आठ", "नौ",
    "শূন্য", "এক", "দুই", "তিন", "চার", "পাঁচ", "ছয়", "সাত", "আট", "নয়",
    "பூஜ்ஜியம்", "ஒன்று", "இரண்டு", "மூன்று", "நான்கு", "ஐந்து", "ஆறு", "ஏழு", "எட்டு", "ஒன்பது",
]
_num = "|".join(sorted(map(re.escape, NUMBER_WORDS), key=len, reverse=True))
NUMBER_RUN = re.compile(rf"(?<!\w)(?:{_num})(?:(?:[\s,.-]|(?<!\w)(?:and|aur|और)(?!\w))+(?:{_num})){{1,}}(?!\w)", re.I)
PAID = re.compile(
    r"\b(sent|transferred|paid|payment (is )?(done|made|complete)|made the payment|done the transfer)\b"
    r"|bhej (diya|diye|di)|transfer kar (diya|diye)|payment (ho gaya|kar diya)"
    r"|भेज दिए|भेज दिया|भेज दी|भेज चुका|भेज चुकी|ट्रांसफर कर दिया|पैसे डाल दिए|पेमेंट कर दिया|पेमेंट हो गया", re.I)


def lang_of(code: str | None) -> str:
    lang = (code or "").split("-")[0]
    return lang if lang in LANG_NAMES else "hi"


def safe(reply: str, code: str | None) -> str:
    """Enforce the hard rules; anything doubtful becomes a stalling line."""
    lang = lang_of(code)
    text = re.sub(r"<[^>]*>|[\[\(].*?[\]\)]|\*", "", reply or "").strip()
    if not text or DIGITS.search(text) or NUMBER_RUN.search(text) or PAID.search(text) or len(text) > 220:
        return STALL.get(lang, STALL["hi"])
    return text


def evidence_complete(tracker: Tracker, exchanges: int) -> bool:
    """Authority claimed, threat made, money asked, and enough conversation recorded."""
    act = tracker.active()
    return exchanges >= MIN_EXCHANGES and {"S1", "S2", "S5"} <= act.keys()


def proof(tracker: Tracker) -> list[dict]:
    """The caller's own words that show the scam: the first line heard for each stage."""
    # same 10-minute window that evidence_complete() judged; hits are kept in time order
    return [{"stage": s, "name": STAGE_NAMES[s], "phrase": hs[0].phrase, "said": hs[0].text}
            for s, hs in sorted(tracker.active().items())]


async def reply(history: list[dict], code: str | None) -> str:
    """Next persona line from Groq. `history` is chat messages: caller = user, persona = assistant."""
    lang = lang_of(code)
    # room for a reasoning model's thinking; the spoken reply itself is capped by safe()
    body = {"model": settings.groq_model, "temperature": 0.8, "max_tokens": 600,
            "messages": [{"role": "system", "content": SYSTEM.format(language=LANG_NAMES[lang])}, *history[-16:]]}
    if "gpt-oss" in settings.groq_model:
        body["reasoning_effort"] = "low"  # fast replies on a live call
    try:
        async with httpx.AsyncClient(timeout=15, transport=transport) as c:
            r = await c.post(GROQ_URL, json=body, headers={"Authorization": f"Bearer {settings.groq_api_key}"})
        r.raise_for_status()
        return safe(r.json()["choices"][0]["message"]["content"], code)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
        # a failing model must be visible: otherwise every turn silently becomes the same stall line
        log.warning("honeypot reply failed, stalling instead: %s %s", e, getattr(getattr(e, "response", None), "text", "")[:200])
        return STALL.get(lang, STALL["hi"])  # never leave the caller in silence
