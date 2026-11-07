"""Measure deterministic planning decisions, not LLM speech quality.

python -m bench.dialogue --strict
"""
import argparse
import json
from pathlib import Path

from app.dialogue import FIELD_CUES, plan_turn


def evaluate(cases: list[dict]) -> dict:
    rows = []
    for case in cases:
        evidence = case.get("evidence", {})
        history = case.get("history", []) + [{"role": "user", "content": case["caller"]}]
        plan = plan_turn(history, evidence)
        expected = (case["action"], case["target"])
        baseline_target = next((field for field in FIELD_CUES if field not in evidence), None)
        baseline = ("clarify" if baseline_target else "listen", baseline_target)
        rows.append({"id": case["id"], "pass": (plan.action, plan.target) == expected,
                     "baseline_pass": baseline == expected, "actual": [plan.action, plan.target]})
    return {"cases": len(rows), "passed": sum(row["pass"] for row in rows),
            "checklist_baseline_passed": sum(row["baseline_pass"] for row in rows), "results": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name("dialogue_cases.json").read_text(encoding="utf-8"))
    result = evaluate(cases)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.strict and result["passed"] != result["cases"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
