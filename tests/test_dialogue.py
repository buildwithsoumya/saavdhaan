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


def test_repeated_app_demands_get_a_different_obstacle():
    first = plan_turn(turns("Install AnyDesk"), {})
    second = plan_turn(turns("Install AnyDesk", "I cannot find it", "Download AnyDesk now"), {})
    assert first.action == second.action == "stall"
    assert first.reason != second.reason


def test_repeated_otp_pressure_does_not_trigger_more_codes():
    plan = plan_turn(turns("OTP please", "Which office?", "Give OTP now"), {})
    assert plan.action == "stall" and "without asking them to resend" in plan.reason


def test_urgency_asks_for_one_step_instead_of_a_new_checklist():
    assert plan_turn(turns("Jaldi karo abhi"), {}).action == "slow_down"


def test_short_acknowledgement_does_not_invent_a_topic():
    assert plan_turn(turns("Okay"), {}).target is None


def test_abandoned_questions_have_a_call_wide_attempt_budget():
    history = turns("Talk", "Your name?", "Listen", "Name please?", "Listen", "Which bank?",
                    "Listen", "Which office?", "Listen")
    assert plan_turn(history, {}).target != "name"
