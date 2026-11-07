import json
from pathlib import Path

import pytest

from app.dialogue import plan_turn

CASES = json.loads((Path(__file__).parents[1] / "bench" / "dialogue_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_planner_scenarios(case):
    plan = plan_turn([{"role": "user", "content": case["caller"]}], case.get("evidence", {}))
    assert (plan.action, plan.target) == (case["action"], case["target"])
