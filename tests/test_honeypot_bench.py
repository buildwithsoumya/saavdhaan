from bench.honeypot import score


def test_score_saved_conversation():
    log = {"history": [{"role": "user", "content": "PNB se Verma bol raha hoon"},
                       {"role": "assistant", "content": "Which bank?"},
                       {"role": "user", "content": "PNB"},
                       {"role": "assistant", "content": "Which bank?"}],
           "evidence": {"bank": "PNB", "name": "Sharma"},
           "evidence_sources": {"bank": "PNB se Verma bol raha hoon", "name": "PNB"}}
    result = score(log)
    assert result["verified_facts"] == 1
    assert result["facts_per_caller_turn"] == 0.5
    assert result["unsupported_facts"] == ["name"]
    assert result["repeated_questions"] == 1
    assert result["repeated_replies"] == 1
    assert result["persona_turns"] == 2
    assert result["replies_with_digits"] == 0
    assert result["payment_claims"] == 0
