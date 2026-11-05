"""Honeypot mode: once a call is clearly a scam (L2+), Saavdhaan can take over the
talking in a neutral persona voice so the elder can step away, keeping the caller
busy while the transcript collects evidence.

Hard rules, enforced in code and not only in the prompt:
- never any digits, so no OTP, account, Aadhaar or card number, real or fake
- never agree that money was sent
- replies are short, plain speech, no tags
"""
import re

import httpx

from app.config import settings
from app.tracker import Tracker

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MIN_EXCHANGES = 4  # persona turns before "enough evidence" can be declared
transport: httpx.AsyncBaseTransport | None = None  # tests swap in httpx.MockTransport

LANG_NAMES = {"hi": "Hindi", "en": "English", "ta": "Tamil", "bn": "Bengali", "kn": "Kannada", "te": "Telugu",
              "mr": "Marathi", "gu": "Gujarati", "ml": "Malayalam", "pa": "Punjabi"}

SYSTEM = """You are role-playing a slow, polite, slightly confused elderly person on a phone call.
The caller is a suspected scammer. Your goal is to keep them talking so they reveal evidence for a police
complaint: their name, the department or company they claim, their badge or case number, the reason they
give, where they want money sent (bank, UPI handle, app), and any links or apps they push.

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

DIGIT_WORDS = re.compile(r"\d|[०-९০-৯௦-௯೦-೯౦-౯]")
PAID = re.compile(r"\b(sent|transferred|paid)\b|भेज दिए|भेज दिया|ट्रांसफर कर दिया|पैसे डाल दिए", re.I)


def lang_of(code: str | None) -> str:
    lang = (code or "").split("-")[0]
    return lang if lang in LANG_NAMES else "hi"


def safe(reply: str, code: str | None) -> str:
    """Enforce the hard rules; anything doubtful becomes a stalling line."""
    lang = lang_of(code)
    text = re.sub(r"<[^>]*>|[\[\(].*?[\]\)]|\*", "", reply or "").strip()
    if not text or DIGIT_WORDS.search(text) or PAID.search(text) or len(text) > 220:
        return STALL.get(lang, STALL["hi"])
    return text


def evidence_complete(tracker: Tracker, exchanges: int) -> bool:
    """Authority claimed, threat made, money asked, and enough conversation recorded."""
    act = tracker.active()
    return exchanges >= MIN_EXCHANGES and {"S1", "S2", "S5"} <= act.keys()


async def reply(history: list[dict], code: str | None) -> str:
    """Next persona line from Groq. `history` is chat messages: caller = user, persona = assistant."""
    lang = lang_of(code)
    body = {"model": settings.groq_model, "temperature": 0.7, "max_tokens": 80,
            "messages": [{"role": "system", "content": SYSTEM.format(language=LANG_NAMES[lang])}, *history[-16:]]}
    async with httpx.AsyncClient(timeout=15, transport=transport) as c:
        r = await c.post(GROQ_URL, json=body, headers={"Authorization": f"Bearer {settings.groq_api_key}"})
    if r.status_code >= 400:
        return STALL.get(lang, STALL["hi"])  # never leave the caller in silence
    return safe(r.json()["choices"][0]["message"]["content"], code)
