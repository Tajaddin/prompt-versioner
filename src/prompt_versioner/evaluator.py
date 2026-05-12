"""Run a prompt version against a JSONL eval set with a pluggable score function.

The score function takes ``(prompt_body, eval_case_input) -> dict`` and the
runner does the bookkeeping. This keeps the eval framework decoupled from any
specific LLM backend.

A typical ``score_fn`` formats the prompt with the case input, calls an LLM,
and returns ``{"passed": bool, "output": str, "elapsed_ms": int, "details": ...}``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class EvalCase:
    case_id: str
    input: dict
    expected: Any | None = None


@dataclass
class EvalReport:
    name: str
    sha: str
    eval_set: str
    pass_count: int
    total_count: int
    cases: list[dict] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.pass_count / max(self.total_count, 1)


ScoreFn = Callable[[str, dict], dict]


def load_jsonl_eval(path: str | Path) -> list[EvalCase]:
    out: list[EvalCase] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            out.append(
                EvalCase(
                    case_id=str(obj.get("case_id") or obj.get("id") or len(out)),
                    input=obj.get("input") or obj,
                    expected=obj.get("expected"),
                )
            )
    return out


@dataclass
class EvalRunner:
    score_fn: ScoreFn

    def run(self, name: str, sha: str, body: str, eval_set: str, cases: list[EvalCase]) -> EvalReport:
        case_results: list[dict] = []
        passed = 0
        for case in cases:
            start = time.perf_counter()
            result = self.score_fn(body, {**case.input, "_expected": case.expected})
            elapsed = int((time.perf_counter() - start) * 1000)
            ok = bool(result.get("passed", False))
            if ok:
                passed += 1
            case_results.append(
                {
                    "case_id": case.case_id,
                    "passed": ok,
                    "elapsed_ms": elapsed,
                    "details": {k: v for k, v in result.items() if k not in {"passed"}},
                }
            )
        return EvalReport(
            name=name,
            sha=sha,
            eval_set=eval_set,
            pass_count=passed,
            total_count=len(cases),
            cases=case_results,
        )


def compare(report_a: EvalReport, report_b: EvalReport, regression_threshold: float = 0.02) -> dict:
    """Returns a regression-style diff between two evals.

    ``regression_threshold`` is the minimum pass-rate drop that counts as a
    regression. A 2-point drop on a 50-case eval is a single case flipping,
    which is usually noise — tune for your eval set.
    """
    if report_a.eval_set != report_b.eval_set:
        raise ValueError("compare() requires same eval_set on both reports")
    delta = report_b.pass_rate - report_a.pass_rate
    regression = -delta >= regression_threshold
    # Per-case flips.
    by_id_a = {c["case_id"]: c["passed"] for c in report_a.cases}
    by_id_b = {c["case_id"]: c["passed"] for c in report_b.cases}
    flipped_passed_to_failed = [cid for cid in by_id_a if by_id_a[cid] and not by_id_b.get(cid, False)]
    flipped_failed_to_passed = [cid for cid in by_id_a if (not by_id_a[cid]) and by_id_b.get(cid, False)]
    return {
        "eval_set": report_a.eval_set,
        "a": {"sha": report_a.sha, "pass_rate": round(report_a.pass_rate, 4)},
        "b": {"sha": report_b.sha, "pass_rate": round(report_b.pass_rate, 4)},
        "delta": round(delta, 4),
        "regression": regression,
        "regression_threshold": regression_threshold,
        "flipped_passed_to_failed": flipped_passed_to_failed,
        "flipped_failed_to_passed": flipped_failed_to_passed,
    }
