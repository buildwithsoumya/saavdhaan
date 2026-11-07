from app.dialogue import plan_turn


def turns(*lines):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": line} for i, line in enumerate(lines)]


def test_payment_destination_takes_priority_and_known_facts_are_skipped():
    assert plan_turn(turns("Transfer money now"), {}).target == "upi_or_account"
    assert plan_turn(turns("Transfer money now"), {"upi_or_account": "x@ybl"}).target == "account_holder"
