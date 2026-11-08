from app.honeypot import Evidence, merge, facts


def test_explicit_correction_then_separate_confirmation_updates_primary():
    evidence, sources = Evidence(), {}
    merge({"bank": "PNB"}, evidence, "The bank is PNB", sources)
    merge({"bank": "SBI"}, evidence, "Correction: the bank is SBI", sources)
    assert evidence.statuses["bank"] == "disputed" and evidence["bank"] == "PNB"
    merge({"bank": "SBI"}, evidence, "Yes, SBI is correct", sources)
    assert evidence["bank"] == "SBI" and evidence.statuses["bank"] == "confirmed"
    assert len(evidence.claim_history["bank"]) == 3
    assert sources["bank"] == "Yes, SBI is correct"
    assert not evidence.alternatives
    assert facts(evidence, sources)[0]["status"] == "confirmed"


def test_retry_and_negated_confirmation_do_not_promote_candidate():
    evidence = Evidence({"bank": "PNB"})
    line = "Correction: SBI is correct"
    merge({"bank": "SBI"}, evidence, line)
    merge({"bank": "SBI"}, evidence, line)
    merge({"bank": "SBI"}, evidence, "SBI is not correct")
    assert evidence["bank"] == "PNB"
    assert evidence.statuses["bank"] == "disputed"


def test_changed_claim_without_explicit_correction_is_not_automatically_promoted():
    evidence = Evidence({"bank": "PNB"})
    merge({"bank": "SBI"}, evidence, "Use SBI")
    merge({"bank": "SBI"}, evidence, "Yes SBI is correct")
    assert evidence["bank"] == "PNB"


def test_question_is_not_confirmation_and_explicit_reversion_can_be_confirmed():
    evidence = Evidence({"bank": "PNB"})
    merge({"bank": "SBI"}, evidence, "Correction: SBI")
    merge({"bank": "SBI"}, evidence, "Is SBI correct?")
    assert evidence["bank"] == "PNB" and evidence.statuses["bank"] == "disputed"
    merge({"bank": "PNB"}, evidence, "Actually use PNB instead")
    merge({"bank": "PNB"}, evidence, "Yes PNB is correct")
    assert evidence.statuses["bank"] == "confirmed" and not evidence.alternatives
