import pytest

from app.dialogue import plan_turn
from app.honeypot import safe, STALL
from app.language_rules import PRIVATE_TERMS, DIGIT_WORDS
from app.honeypot import recovery_reply, GOODBYE


@pytest.mark.parametrize("language", PRIVATE_TERMS)
def test_native_private_terms_stall_in_each_language(language):
    plan = plan_turn([{"role": "user", "content": PRIVATE_TERMS[language][0]}], {})
    assert plan.action == "stall" and plan.target is None


@pytest.mark.parametrize("language", DIGIT_WORDS)
def test_native_digit_runs_cannot_be_spoken(language):
    line = " ".join(DIGIT_WORDS[language][1:4])
    assert safe(line, language + "-IN") == STALL.get(language, STALL["hi"])


@pytest.mark.parametrize("language", PRIVATE_TERMS)
def test_recovery_and_farewell_are_safe_and_available_in_each_language(language):
    recovery = recovery_reply([{"role": "user", "content": PRIVATE_TERMS[language][0]}], language, {"name": "Verma"})
    assert safe(recovery, language) == recovery
    assert safe(GOODBYE[language], language) == GOODBYE[language]
