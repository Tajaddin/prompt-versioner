# prompt-versioner

> Git-style prompt management with named versions, deterministic A/B traffic splits, and regression detection on a saved eval set. **Stdlib + click + pydantic only.** Single SQLite file = your prompt repo.

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE) [![Tests](https://img.shields.io/badge/tests-22%20passing-brightgreen)](#tests) [![Python](https://img.shields.io/badge/python-3.10%2B-blue)]()

## Why this exists

Most teams version prompts in a Google Doc, a spreadsheet, or `prompts/*.py` files in their main repo. The pain shows up when you need to (a) ship a "production" prompt while testing a candidate, (b) compare two versions on a held-out eval, or (c) revert when an eval CI run regresses.

`prompt-versioner` is one SQLite file plus a CLI that gives you the git mental model on top:

* **Commits** are content-addressed (`sha` = sha1 of body, truncated to 12 chars). Identical bodies dedupe automatically: re-committing one returns the version already stored, with its original message, author and timestamp.
* **Tags** point at one sha per name: `production`, `staging`, `experiment-3`. Move them like git tags.
* **Eval results** are durable rows attached to `(name, sha, eval_set)` — `prompt compare` exits non-zero on regression so it slots into CI.
* **A/B splits** are deterministic from a user/request key, so the same caller always lands on the same arm — no leaks, no flapping.

## Hero features

| | What you can do |
|---|---|
| Versioning | `prompt commit my_clf --file v1.txt -m "tighter rubric"` |
| Inspection | `prompt log my_clf`, `prompt show my_clf 1a2b3c4d` |
| Diff between versions | `prompt diff my_clf 1a2b3c4d 5e6f7a8b` (unified diff) |
| Tagging | `prompt tag my_clf production 5e6f7a8b` |
| Eval recording | `prompt eval-record my_clf 5e6f7a8b smoke --pass 48 --total 50` |
| Regression check | `prompt compare my_clf 1a2b3c4d 5e6f7a8b smoke --threshold 0.02` (exit 2 on regression) |
| A/B traffic split | `choose(TrafficSplit(arms=(("sha-a", 0.7), ("sha-b", 0.3))), user_key)` |

Every feature has at least one test in [`tests/`](tests/).

## Quickstart

```bash
pip install -e .
```

```bash
# Commit two versions of a prompt
prompt commit greeter --body "Hi, how can I help?" -m "v1"
prompt commit greeter --body "Hello! What can I do for you today?" -m "v2-friendlier"

# Inspect
prompt log greeter
# 23c116d63353  -  v2-friendlier
# 1e6c911beb5b  -  v1

# Tag the first as production
prompt tag greeter production 1e6c911beb5b

# Record evals on a held-out set
prompt eval-record greeter 1e6c911beb5b smoke --pass 45 --total 50
prompt eval-record greeter 23c116d63353 smoke --pass 48 --total 50

# Compare — exits 0 (no regression)
prompt compare greeter 1e6c911beb5b 23c116d63353 smoke
```

## Library use

### Versioning

```python
from prompt_versioner import PromptStore

store = PromptStore("prompts.db")
v = store.commit("classifier", "Classify the input as one of {positive, negative, neutral}.", message="initial")
print(v.sha)   # → 12-char content-addressed id

# Tag the production version
store.tag("classifier", "production", v.sha)

# Resolve the tag anywhere
resolved = store.resolve_tag("classifier", "production")
print(resolved.body)
```

### A/B traffic split

```python
from prompt_versioner import TrafficSplit, choose

split = TrafficSplit(arms=(
    ("1a2b3c4d", 0.9),   # current production
    ("5e6f7a8b", 0.1),   # candidate
))

# Same user always lands on the same arm — sticky A/B.
arm_sha = choose(split, arm_key=request.user_id)
prompt_body = store.get("classifier", arm_sha).body
```

### Regression detection on a saved eval

```python
from prompt_versioner import EvalCase, EvalRunner
from prompt_versioner.evaluator import compare

def score_fn(prompt_body: str, case: dict) -> dict:
    response = call_your_llm(prompt_body, case["input"])
    return {"passed": response.strip() == case["_expected"]}

cases = [EvalCase(case_id="c1", input={"input": "great movie"}, expected="positive"), ...]
runner = EvalRunner(score_fn=score_fn)

report_a = runner.run("classifier", sha_a, prompt_a_body, "smoke", cases)
report_b = runner.run("classifier", sha_b, prompt_b_body, "smoke", cases)

diff = compare(report_a, report_b, regression_threshold=0.02)
if diff["regression"]:
    raise SystemExit(f"prompt regressed: {diff['delta']:.4f}")
```

## CI integration

Hook `prompt compare` into your pre-merge CI:

```bash
# In your CI workflow:
SHA_PROD=$(git ls-remote --tags origin | grep production | head -1)  # or however you track it
SHA_HEAD=$(prompt commit classifier --file prompts/classifier.txt -m "$(git log -1 --pretty=%s)" | cut -f2)

# Run your eval against both shas and record results
python eval/run.py --prompt-sha $SHA_PROD  > /dev/null
python eval/run.py --prompt-sha $SHA_HEAD  > /dev/null

prompt compare classifier $SHA_PROD $SHA_HEAD smoke --threshold 0.02
# Exit 2 if regressed → CI fails.
```

## CLI reference

```
prompt commit NAME [--file PATH | --body TEXT] [-m MSG] [--author NAME]
prompt show NAME SHA
prompt log NAME [--limit N]
prompt diff NAME SHA_A SHA_B
prompt tag NAME LABEL SHA
prompt tags NAME
prompt eval-record NAME SHA EVAL_SET --pass N --total N [--notes ...]
prompt compare NAME SHA_A SHA_B EVAL_SET [--threshold 0.02]
```

Store path can be overridden via `PROMPT_STORE_PATH` (default: `.prompts.db` in CWD).

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

```
22 passed in 0.15s
```

Tests cover:

* `commit` is content-addressed, deduplicates identical bodies and returns the stored version on a duplicate
* `tag` insert + update + unknown-sha rejection
* `record_eval` rejects out-of-range counts; `latest_eval` returns the most recent
* DB persists across handles
* `TrafficSplit` is deterministic for the same key and respects weights within ±2 points over 2000 keys
* `EvalRunner` invokes the score function per case and counts pass/fail correctly
* `compare` flags regressions, lists per-case flips, rejects mismatched eval sets

## Project layout

```
.
├── src/prompt_versioner/
│   ├── __init__.py
│   ├── store.py        # SQLite PromptStore: commit / tag / record_eval / resolve_tag
│   ├── traffic.py      # Deterministic A/B TrafficSplit + choose()
│   ├── evaluator.py    # EvalRunner + compare() (regression detection)
│   └── cli.py          # `prompt` CLI (click)
└── tests/              # 22 pytest cases
```

## Limitations

**Single-machine.** SQLite is local. For team-shared prompt history, point `PROMPT_STORE_PATH` at a network mount, or back it with Litestream / replicate the file.

**No prompt linting.** The body is whatever string you pass. A natural v0.2 would lint for: unbalanced template variables, trailing whitespace, banned words, prompt-injection patterns at commit time.

**Eval recording is detached from execution.** `EvalRunner` is one option but you can also write eval results from any other test framework via `record_eval`. This is intentional — the version store doesn't need to know how you ran the eval.

**A/B split is fixed-arm.** No contextual bandits, no automatic ramping. Use `TrafficSplit` as a building block; the policy layer is your code.

## License

MIT — see [LICENSE](LICENSE).
