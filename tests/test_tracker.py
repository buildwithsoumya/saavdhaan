import yaml

from app.tracker import DATA, Tracker, keyterms


def stages(text):
    tr = Tracker()
    tr.feed(text, t=0)
    return set(tr.active(0))


def test_codemix_hindi_english_digital_arrest_script():
    tr = Tracker()
    tr.feed("नमस्ते, मैं Mumbai crime branch से inspector बोल रहा हूँ", t=0)
    assert tr.level(0) == 0
    tr.feed("आपके Aadhaar पर money laundering का case registered है, arrest warrant निकला है", t=10)
    assert set(tr.active(10)) == {"S1", "S2"} and tr.level(10) == 1
    tr.feed("ये confidential है, किसी को मत बताना, video call पर रहिए", t=20)
    assert tr.level(20) == 2
    tr.feed("तुरंत अपने पैसे safe account में transfer कीजिए", t=30)
    assert tr.level(30) == 3
    exp = tr.explain(30)
    assert exp["level_name"] == "Stop"
    assert {h["phrase"] for h in exp["stages"]["S5"]} == {"safe account", "transfer"}
    assert exp["stages"]["S1"][0]["t"] == 0


def test_each_stage_counts_once():
    tr = Tracker()
    for i in range(5):
        tr.feed("police police CBI", t=i)
    assert tr.level(5) == 0 and set(tr.active(5)) == {"S1"}


def test_genuine_bank_never_share_otp_does_not_trigger():
    tr = Tracker()
    tr.feed("Main bank ke fraud prevention desk se officer bol raha hoon", t=0)
    tr.feed("Your card is suspended for safety. Never share your OTP or CVV with anyone.", t=5)
    tr.feed("आपका OTP किसी के साथ भी शेयर मत कीजिए", t=10)
    tr.feed("OTP kisi ko mat batana, bank kabhi OTP nahi maangta", t=15)
    assert "S5" not in tr.active(15)
    assert tr.level(15) <= 1
    assert {h["phrase"] for h in tr.explain(15)["ignored"]} >= {"otp", "cvv"}


def test_negation_does_not_leak_across_clause_break():
    assert "S5" in stages("OTP batao warna arrest nahi rukega")
    assert "S5" in stages("OTP बताइए वरना गिरफ्तारी नहीं रुकेगी")
    assert "S5" in stages("Don't worry. Tell me the OTP now")


def test_l3_needs_extraction_after_authority_or_fear():
    tr = Tracker()
    tr.feed("please do the UPI transfer", t=0)  # extraction first
    tr.feed("urgent", t=1)
    assert tr.level(1) == 1
    tr.feed("this is the police", t=2)
    assert tr.level(2) == 2  # S5 came before S1, so not L3
    tr.feed("send the OTP", t=3)
    assert tr.level(3) == 3


def test_rolling_window_drops_old_stages():
    tr = Tracker(window_s=600)
    tr.feed("CBI officer here", t=0)
    tr.feed("arrest warrant against you", t=100)
    assert tr.level(100) == 1
    assert tr.level(650) == 0  # S1 aged out, only S2 left
    tr.feed("CBI again", t=700)
    assert tr.level(700) == 1


def test_tamil_script():
    got = stages("நான் சிபிஐ அதிகாரி. உங்கள் மீது வழக்கு உள்ளது, உடனே பணம் அனுப்புங்கள்")
    assert got == {"S1", "S2", "S4", "S5"}
    assert "S5" not in stages("உங்கள் ஓடிபி யாருக்கும் பகிர வேண்டாம்")
    assert "S5" not in stages("ஓடிபி யாரிடமும் பகிராதீர்கள்")  # negative-imperative suffix


def test_bengali_script():
    got = stages("আমি পুলিশ থেকে বলছি, আপনার নামে মামলা আছে, এখনই টাকা পাঠান")
    assert got == {"S1", "S2", "S4", "S5"}
    assert "S5" not in stages("ওটিপি কাউকে দেবেন না")


def test_kannada_telugu_marathi_samples():
    assert stages("ನಾನು ಸಿಬಿಐ ಅಧಿಕಾರಿ, ನಿಮ್ಮ ಮೇಲೆ ವಾರಂಟ್ ಇದೆ") == {"S1", "S2"}
    assert "S5" not in stages("ಒಟಿಪಿ ಯಾರಿಗೂ ಹೇಳಬೇಡಿ")
    assert stages("నేను పోలీస్ అధికారి, మీ మీద కేసు ఉంది") == {"S1", "S2"}
    assert "S5" not in stages("ఓటీపీ ఎవరికీ చెప్పకండి")
    assert stages("मी पोलीस अधिकारी बोलतोय, तुम्हाला अटक होईल, लगेच पैसे पाठवा") == {"S1", "S2", "S4", "S5"}


def test_hard_negatives_stay_below_l2():
    courier = Tracker()
    courier.feed("Speedpost se delivery boy bol raha hoon, aapka order bahar hai", t=0)
    courier.feed("delivery ke liye OTP bata dijiye", t=5)
    assert courier.level(5) == 0

    relative = Tracker()
    relative.feed("Papa main bol raha hoon, college fees aaj hi bharni hai", t=0)
    relative.feed("please UPI se paise bhejo", t=5)
    assert relative.level(5) == 1


def test_stage_following_fires_once_after_s1_and_s2():
    tr = Tracker()
    tr.feed("CBI se bol raha hoon", t=0)
    assert not tr.take_follow_signal(0)
    tr.feed("aapke naam pe arrest warrant hai", t=1)
    assert tr.take_follow_signal(1)
    assert not tr.take_follow_signal(2)


def test_keyterm_lists_fit_api_limit():
    base, extraction = keyterms("base"), keyterms("extraction")
    assert 0 < len(base) <= 200 and 0 < len(extraction) <= 200
    assert "OTP" in extraction and "OTP" not in base


def test_every_non_hindi_native_list_is_flagged_for_review():
    langs = yaml.safe_load((DATA / "lexicons.yaml").read_text(encoding="utf-8"))["languages"]
    for code, block in langs.items():
        if code not in ("hi", "en"):
            assert block.get("needs_native_review") is True, code


def test_genuine_warnings_and_verification_visits_stay_quiet():
    assert "S5" not in stages("If anyone asks for an OTP, don't give it, and never transfer money.")
    assert "S5" not in stages("नमस्ते, मैं थाने से बोल रहा हूँ, आपके पासपोर्ट के पुलिस वेरिफिकेशन के लिए।")
    assert "S5" in stages("अभी के अभी वेरिफिकेशन अमाउंट भेजिए")
