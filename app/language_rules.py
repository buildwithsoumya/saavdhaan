"""Explicit lexical safeguards; these are coverage rules, not language understanding.

Native speaker review is still needed for dialects and transliterated variants.
"""
import re
import unicodedata

PRIVATE_TERMS = {
    "en": ("password", "OTP", "PIN", "CVV", "Aadhaar"),
    "hi": ("पासवर्ड", "ओटीपी", "पिन", "आधार"),
    "ta": ("கடவுச்சொல்", "கடவுச்சொல்லை", "ஓடிபி", "ஆதார்"),
    "bn": ("পাসওয়ার্ড", "ওটিপি", "পিন", "আধার"),
    "kn": ("ಪಾಸ್ವರ್ಡ್", "ಪಾಸ್‌ವರ್ಡ್", "ಒಟಿಪಿ", "ಆಧಾರ್"),
    "te": ("పాస్వర్డ్", "పాస్‌వర్డ్", "ఓటీపీ", "ఆధార్"),
    "mr": ("पासवर्ड", "ओटीपी", "पिन", "आधार"),
    "gu": ("પાસવર્ડ", "ઓટીપી", "પિન", "આધાર"),
    "ml": ("പാസ്‌വേഡ്", "പാസ്വേഡ്", "ഒടിപി", "ആധാർ"),
    "pa": ("ਪਾਸਵਰਡ", "ਓਟੀਪੀ", "ਪਿੰਨ", "ਆਧਾਰ"),
}
DIGIT_WORDS = {
    "en": "zero one two three four five six seven eight nine".split(),
    "hi": "शून्य एक दो तीन चार पांच पाँच छह सात आठ नौ".split(),
    "ta": "பூஜ்ஜியம் ஒன்று இரண்டு மூன்று நான்கு ஐந்து ஆறு ஏழு எட்டு ஒன்பது".split(),
    "bn": "শূন্য এক দুই তিন চার পাঁচ ছয় সাত আট নয়".split(),
    "kn": "ಸೊನ್ನೆ ಶೂನ್ಯ ಒಂದು ಎರಡು ಮೂರು ನಾಲ್ಕು ಐದು ಆರು ಏಳು ಎಂಟು ಒಂಬತ್ತು".split(),
    "te": "సున్నా ఒకటి రెండు మూడు నాలుగు ఐదు ఆరు ఏడు ఎనిమిది తొమ్మిది".split(),
    "mr": "शून्य एक दोन तीन चार पाच सहा सात आठ नऊ".split(),
    "gu": "શૂન્ય એક બે ત્રણ ચાર પાંચ છ સાત આઠ નવ".split(),
    "ml": "പൂജ്യം ഒന്ന് രണ്ട് മൂന്ന് നാല് അഞ്ച് ആറ് ഏഴ് എട്ട് ഒമ്പത്".split(),
    "pa": "ਸਿਫਰ ਜ਼ੀਰੋ ਇੱਕ ਦੋ ਤਿੰਨ ਚਾਰ ਪੰਜ ਛੇ ਸੱਤ ਅੱਠ ਨੌਂ".split(),
}
NEUTRAL_RECOVERY = {
    "en": "Sorry, please explain slowly.", "hi": "माफ़ कीजिए, आप धीरे से समझाइए।",
    "ta": "மன்னிக்கவும், மெதுவாக விளக்குங்கள்.", "bn": "মাফ করবেন, ধীরে বুঝিয়ে বলুন।",
    "kn": "ಕ್ಷಮಿಸಿ, ನಿಧಾನವಾಗಿ ವಿವರಿಸಿ.", "te": "క్షమించండి, నెమ్మదిగా వివరించండి.",
    "mr": "माफ करा, हळू समजावून सांगा.", "gu": "માફ કરશો, ધીમેથી સમજાવો.",
    "ml": "ക്ഷമിക്കണം, പതുക്കെ വിശദീകരിക്കൂ.", "pa": "ਮਾਫ਼ ਕਰਨਾ, ਹੌਲੀ ਸਮਝਾਓ।",
}
REPEAT_RECOVERY = {
    "en": "Please repeat the last instruction.", "hi": "आखिरी बात फिर से बताइए।",
    "ta": "கடைசியாக சொன்னதை மீண்டும் சொல்லுங்கள்.", "bn": "শেষ কথাটা আবার বলুন।",
    "kn": "ಕೊನೆಯ ಮಾತನ್ನು ಮತ್ತೆ ಹೇಳಿ.", "te": "చివరి మాట మళ్లీ చెప్పండి.",
    "mr": "शेवटचं पुन्हा सांगा.", "gu": "છેલ્લી વાત ફરી કહો.",
    "ml": "അവസാനം പറഞ്ഞത് വീണ്ടും പറയൂ.", "pa": "ਆਖਰੀ ਗੱਲ ਦੁਬਾਰਾ ਦੱਸੋ।",
}


def normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold().replace("’", "'")


def has_private_term(text: str) -> bool:
    text = normalize(text)
    return any((re.search(r"(?<![a-z])" + re.escape(term.lower()) + r"(?![a-z])", text)
                if term.isascii() else normalize(term) in text)
               for terms in PRIVATE_TERMS.values() for term in terms)
