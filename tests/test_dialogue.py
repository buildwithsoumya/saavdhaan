from app.dialogue import plan_turn


def turns(*lines):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": line} for i, line in enumerate(lines)]


def test_payment_destination_takes_priority_and_known_facts_are_skipped():
    assert plan_turn(turns("Transfer money now"), {}).target == "upi_or_account"
    assert plan_turn(turns("Transfer money now"), {"upi_or_account": "x@ybl"}).target == "account_holder"


def test_ignored_question_cools_down_for_two_replies():
    plan = plan_turn(turns("Hello", "What is your name?", "Just listen to me"), {})
    assert plan.target != "name"


def test_refusal_changes_strategy_instead_of_interrogating():
    plan = plan_turn(turns("Stop asking questions, I am not telling you"), {})
    assert plan.action == "deescalate" and plan.target is None


def test_threats_take_priority_over_payment_checklist():
    assert plan_turn(turns("Transfer now or we will arrest you"), {}).action == "calm"


def test_direct_questions_get_an_answer_before_an_evidence_request():
    assert plan_turn(turns("Can you hear me?"), {}).action == "answer"


def test_isolation_demand_never_becomes_a_promise_of_secrecy():
    plan = plan_turn(turns("Don't tell anyone, stay alone"), {})
    assert "Do not promise secrecy" in plan.reason
