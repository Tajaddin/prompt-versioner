"""TrafficSplit + EvalRunner + compare() tests."""

from __future__ import annotations

import pytest

from prompt_versioner import EvalCase, EvalRunner, TrafficSplit, choose
from prompt_versioner.evaluator import compare


def test_traffic_split_rejects_empty() -> None:
    with pytest.raises(ValueError):
        TrafficSplit(arms=())


def test_traffic_split_rejects_negative_weight() -> None:
    with pytest.raises(ValueError):
        TrafficSplit(arms=(("a", -1.0),))


def test_traffic_split_deterministic_for_same_key() -> None:
    split = TrafficSplit(arms=(("a", 0.5), ("b", 0.5)))
    keys = [f"user-{i}" for i in range(100)]
    out_1 = [choose(split, k) for k in keys]
    out_2 = [choose(split, k) for k in keys]
    assert out_1 == out_2


def test_traffic_split_respects_weights_roughly() -> None:
    split = TrafficSplit(arms=(("a", 0.8), ("b", 0.2)))
    keys = [f"user-{i}" for i in range(2000)]
    a_count = sum(1 for k in keys if choose(split, k) == "a")
    rate_a = a_count / 2000
    # Hash-based split; expect within ~2 percentage points of 0.80.
    assert 0.78 < rate_a < 0.82


def test_traffic_split_unequal_weights_dont_need_to_sum_to_one() -> None:
    split = TrafficSplit(arms=(("a", 3), ("b", 1)))
    keys = [f"user-{i}" for i in range(2000)]
    a_count = sum(1 for k in keys if choose(split, k) == "a")
    rate_a = a_count / 2000
    assert 0.72 < rate_a < 0.78


def test_eval_runner_calls_score_fn_per_case() -> None:
    def score_fn(body: str, case_input: dict) -> dict:
        return {"passed": case_input["x"] > 0, "output": case_input["x"]}

    runner = EvalRunner(score_fn=score_fn)
    cases = [EvalCase(case_id=str(i), input={"x": i - 1}) for i in range(5)]
    report = runner.run("p", "abc123", "the body", "smoke", cases)
    assert report.pass_count == 3   # x = 0, 1, 2, 3 → passes when > 0, so x=1,2,3 → 3 cases
    assert report.total_count == 5


def test_compare_detects_regression() -> None:
    def score(body, case_input):
        return {"passed": case_input["x"] > 0}

    cases = [EvalCase(case_id=str(i), input={"x": i}) for i in range(5)]
    runner = EvalRunner(score_fn=score)
    # Both reports run identical eval on identical body; we manually mark
    # report_b worse by flipping a couple of "passed" entries.
    a = runner.run("p", "aaa", "body", "smoke", cases)
    b = runner.run("p", "bbb", "body", "smoke", cases)
    # Manually degrade B.
    b.cases[1]["passed"] = False
    b.cases[2]["passed"] = False
    b.pass_count = sum(1 for c in b.cases if c["passed"])
    diff = compare(a, b, regression_threshold=0.1)
    assert diff["regression"] is True
    assert diff["delta"] < 0


def test_compare_no_regression_when_pass_rate_equal() -> None:
    def score(body, case_input):
        return {"passed": True}

    cases = [EvalCase(case_id=str(i), input={"x": i}) for i in range(3)]
    runner = EvalRunner(score_fn=score)
    a = runner.run("p", "aaa", "body", "smoke", cases)
    b = runner.run("p", "bbb", "body", "smoke", cases)
    diff = compare(a, b)
    assert diff["regression"] is False
    assert diff["delta"] == 0


def test_compare_lists_flipped_cases() -> None:
    def score(body, case_input):
        return {"passed": True}

    cases = [EvalCase(case_id=str(i), input={}) for i in range(3)]
    runner = EvalRunner(score_fn=score)
    a = runner.run("p", "aaa", "body", "smoke", cases)
    b = runner.run("p", "bbb", "body", "smoke", cases)
    b.cases[0]["passed"] = False
    diff = compare(a, b)
    assert diff["flipped_passed_to_failed"] == ["0"]


def test_compare_rejects_different_eval_sets() -> None:
    def score(body, case_input):
        return {"passed": True}

    cases = [EvalCase(case_id="0", input={})]
    runner = EvalRunner(score_fn=score)
    a = runner.run("p", "aaa", "body", "smoke", cases)
    b = runner.run("p", "bbb", "body", "other", cases)
    with pytest.raises(ValueError):
        compare(a, b)
