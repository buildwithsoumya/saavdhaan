"""Follow-ups from reviewing PRs #20 and #21."""
from app import honeypot
from app.dialogue import plan_turn
from app.honeypot import Evidence
from app.tracker import Tracker


def test_every_canned_line_passes_the_safety_filter():
    lines = [*honeypot.GOODBYE.values(), *honeypot.STALL.values(), *(l for ls in honeypot.STALL_MORE.values() for l in ls)]
    lines += [honeypot.recovery_reply([{"role": "user", "content": text}], lang, {})
              for text in ("Transfer money now", "arrest warrant hai", "OTP batao", "hello") for lang in ("en", "hi")]
    for line in lines:
        assert honeypot.safe(line, "hi-IN") == line or line in honeypot.STALL.values(), line


def test_latin_keywords_need_word_boundaries():
    assert plan_turn([{"role": "user", "content": "This is Abhishek from the bank."}], {}).action != "slow_down"
    assert plan_turn([{"role": "user", "content": "It needs some skill to fill this form."}], {}).action != "calm"


def test_a_quiet_turn_is_followed_by_an_evidence_question():
    history = [{"role": "user", "content": "You will be arrested."},
               {"role": "assistant", "content": "Oh no, please don't be angry, I am scared."},  # asked nothing
               {"role": "user", "content": "Arrest warrant is ready, transfer money now."}]
    plan = plan_turn(history, {})
    assert plan.action == "clarify" and plan.target == "upi_or_account"


def test_a_contradiction_does_not_block_closing():
    tr = Tracker()
    tr.feed("CBI officer, arrest warrant hai, UPI se transfer karo")
    ev = Evidence({"upi_or_account": "a@ybl"})
    ev.alternatives["upi_or_account"] = [{"value": "b@ybl", "said": "no, b@ybl"}]
    assert honeypot.evidence_complete(tr, honeypot.MIN_EXCHANGES, ev)
