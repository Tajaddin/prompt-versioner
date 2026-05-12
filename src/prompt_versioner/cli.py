"""``prompt`` CLI — commit / show / log / tag / diff / eval-record / compare.

The CLI keeps a single store at ``.prompts.db`` in the current working
directory by default. Override via ``PROMPT_STORE_PATH``.
"""

from __future__ import annotations

import difflib
import json
import os
import sys
from pathlib import Path

import click

from prompt_versioner.store import PromptStore


def _store() -> PromptStore:
    path = os.environ.get("PROMPT_STORE_PATH", ".prompts.db")
    return PromptStore(path)


@click.group()
def cli() -> None:
    """Git-style prompt versioner."""


# ---------------------------------------------------------------------------
# commit / show / log
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name")
@click.option("--file", "file_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--body", type=str, default=None, help="Inline body (mutually exclusive with --file)")
@click.option("-m", "--message", default="")
@click.option("--author", default="")
def commit(name: str, file_path: str | None, body: str | None, message: str, author: str) -> None:
    """Commit a new prompt version under NAME."""
    if not (file_path or body):
        raise click.UsageError("Provide either --file or --body")
    if file_path and body:
        raise click.UsageError("--file and --body are mutually exclusive")
    text = body if body is not None else Path(file_path).read_text(encoding="utf-8")
    store = _store()
    v = store.commit(name, text, message=message, author=author)
    click.echo(f"{v.name}\t{v.sha}\t{v.message}")


@cli.command()
@click.argument("name")
@click.argument("sha")
def show(name: str, sha: str) -> None:
    """Print the body of a (name, sha)."""
    v = _store().get(name, sha)
    if not v:
        raise click.ClickException(f"not found: {name}@{sha}")
    click.echo(v.body)


@cli.command(name="log")
@click.argument("name")
@click.option("--limit", default=20, type=int)
def log_cmd(name: str, limit: int) -> None:
    """Show recent commits for NAME, newest first."""
    for v in _store().history(name, limit=limit):
        click.echo(f"{v.sha}\t{v.author or '-'}\t{v.message}")


# ---------------------------------------------------------------------------
# tag / diff
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("name")
@click.argument("label")
@click.argument("sha")
def tag(name: str, label: str, sha: str) -> None:
    """Point LABEL at (NAME, SHA). Common labels: production / staging / vN."""
    t = _store().tag(name, label, sha)
    click.echo(f"tagged {t.name} :: {t.label} -> {t.target_sha}")


@cli.command()
@click.argument("name")
@click.argument("sha_a")
@click.argument("sha_b")
def diff(name: str, sha_a: str, sha_b: str) -> None:
    """Unified diff between two SHAs of NAME."""
    s = _store()
    a = s.get(name, sha_a)
    b = s.get(name, sha_b)
    if not a or not b:
        raise click.ClickException("one of the SHAs was not found")
    diff_lines = difflib.unified_diff(
        a.body.splitlines(keepends=True),
        b.body.splitlines(keepends=True),
        fromfile=f"{name}@{sha_a}",
        tofile=f"{name}@{sha_b}",
        n=3,
    )
    click.echo("".join(diff_lines))


# ---------------------------------------------------------------------------
# eval record + compare
# ---------------------------------------------------------------------------


@cli.command(name="eval-record")
@click.argument("name")
@click.argument("sha")
@click.argument("eval_set")
@click.option("--pass", "pass_count", type=int, required=True)
@click.option("--total", "total_count", type=int, required=True)
@click.option("--notes", default=None)
def eval_record(name: str, sha: str, eval_set: str, pass_count: int, total_count: int, notes: str | None) -> None:
    """Record an eval result for (NAME, SHA) on EVAL_SET."""
    s = _store()
    s.record_eval(name, sha, eval_set, pass_count, total_count, notes=notes)
    rate = pass_count / total_count if total_count else 0.0
    click.echo(f"{name}@{sha}\t{eval_set}\tpass_rate={rate:.4f}\t({pass_count}/{total_count})")


@cli.command()
@click.argument("name")
@click.argument("sha_a")
@click.argument("sha_b")
@click.argument("eval_set")
@click.option("--threshold", "regression_threshold", type=float, default=0.02)
def compare(name: str, sha_a: str, sha_b: str, eval_set: str, regression_threshold: float) -> None:
    """Compare the latest recorded eval results for two SHAs."""
    s = _store()
    a = s.latest_eval(name, sha_a, eval_set)
    b = s.latest_eval(name, sha_b, eval_set)
    if not a or not b:
        raise click.ClickException("missing eval result for one or both SHAs (record with `prompt eval-record`)")
    delta = b["pass_rate"] - a["pass_rate"]
    regression = (-delta) >= regression_threshold
    out = {
        "name": name,
        "eval_set": eval_set,
        "a": {"sha": sha_a, **a},
        "b": {"sha": sha_b, **b},
        "delta": round(delta, 4),
        "regression": regression,
        "threshold": regression_threshold,
    }
    click.echo(json.dumps(out, indent=2))
    if regression:
        sys.exit(2)  # CI-friendly non-zero exit


# ---------------------------------------------------------------------------
# tags listing
# ---------------------------------------------------------------------------


@cli.command(name="tags")
@click.argument("name")
def tags_cmd(name: str) -> None:
    """List tags for NAME."""
    for t in _store().tags(name):
        click.echo(f"{t.label}\t{t.target_sha}")


if __name__ == "__main__":
    cli()
