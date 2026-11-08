"""Hindi question words ending in a vowel sign (क्या, क्यों) must be recognised; \\b never matches after a matra."""
from app.dialogue import plan_turn
from app.honeypot import Evidence


def test_hindi_direct_questions_are_answered_without_a_question_mark():
    for caller in ("क्या आप मेरी बात सुन रहे हैं", "क्यों नहीं भेज रहे पैसा", "कौन बोल रहा है", "kya aap sun rahe ho"):
        assert plan_turn([{"role": "user", "content": caller}], {}).action == "answer", caller
    assert plan_turn([{"role": "user", "content": "क्यारी में पौधे लगे हैं"}], {}).action != "answer"  # not the word क्या


def test_a_hindi_question_is_not_a_confirmation():
    ev = Evidence()
    ev.observe("upi_or_account", "a@ybl", "UPI a@ybl पर भेजो", {}, 1)
    ev.observe("upi_or_account", "b@ybl", "correction, actually UPI b@ybl है", {}, 2)
    ev.observe("upi_or_account", "b@ybl", "क्या b@ybl सही है", {}, 3)  # STT often drops the "?"
    assert ev.statuses["upi_or_account"] != "confirmed"
    assert ev.alternatives.get("upi_or_account")  # the conflict stays on the proof card
