"""PromptStore tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from prompt_versioner import PromptStore


def test_commit_returns_version_with_sha() -> None:
    s = PromptStore(":memory:")
    v = s.commit("classifier", "you are a helpful assistant", message="initial")
    assert v.name == "classifier"
    assert len(v.sha) == 12
    assert v.body == "you are a helpful assistant"


def test_identical_commits_dedup() -> None:
    s = PromptStore(":memory:")
    v1 = s.commit("p", "same body")
    v2 = s.commit("p", "same body")
    assert v1.sha == v2.sha
    assert len(s.history("p")) == 1


def test_duplicate_commit_returns_the_stored_version() -> None:
    s = PromptStore(":memory:")
    v1 = s.commit("p", "same body", message="first", author="ana", meta={"v": 1})
    v2 = s.commit("p", "same body", message="second", author="ben", meta={"v": 2})
    assert v2 == v1
    assert v2 == s.get("p", v1.sha)


def test_different_bodies_get_different_shas() -> None:
    s = PromptStore(":memory:")
    v1 = s.commit("p", "body A")
    v2 = s.commit("p", "body B")
    assert v1.sha != v2.sha


def test_history_newest_first() -> None:
    s = PromptStore(":memory:")
    s.commit("p", "v1", message="first")
    s.commit("p", "v2", message="second")
    s.commit("p", "v3", message="third")
    history = s.history("p")
    assert [v.message for v in history] == ["third", "second", "first"]


def test_tag_resolves_to_version() -> None:
    s = PromptStore(":memory:")
    v = s.commit("p", "the body")
    s.tag("p", "production", v.sha)
    resolved = s.resolve_tag("p", "production")
    assert resolved is not None
    assert resolved.sha == v.sha


def test_tag_updates_when_repointed() -> None:
    s = PromptStore(":memory:")
    v1 = s.commit("p", "v1")
    v2 = s.commit("p", "v2")
    s.tag("p", "production", v1.sha)
    s.tag("p", "production", v2.sha)
    assert s.resolve_tag("p", "production").sha == v2.sha


def test_tag_unknown_sha_rejected() -> None:
    s = PromptStore(":memory:")
    with pytest.raises(ValueError):
        s.tag("p", "production", "deadbeef0000")


def test_record_eval_and_latest() -> None:
    s = PromptStore(":memory:")
    v = s.commit("p", "body")
    s.record_eval("p", v.sha, "smoke", pass_count=45, total_count=50, notes="run-1")
    s.record_eval("p", v.sha, "smoke", pass_count=48, total_count=50, notes="run-2")
    latest = s.latest_eval("p", v.sha, "smoke")
    assert latest["pass_count"] == 48
    assert latest["pass_rate"] == pytest.approx(0.96)


def test_record_eval_rejects_bad_counts() -> None:
    s = PromptStore(":memory:")
    v = s.commit("p", "body")
    with pytest.raises(ValueError):
        s.record_eval("p", v.sha, "smoke", pass_count=10, total_count=5)
    with pytest.raises(ValueError):
        s.record_eval("p", v.sha, "smoke", pass_count=-1, total_count=5)


def test_persists_across_handles() -> None:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    try:
        s1 = PromptStore(path)
        v = s1.commit("classifier", "the body", message="init")
        s1.tag("classifier", "production", v.sha)
        s1.close()

        s2 = PromptStore(path)
        history = s2.history("classifier")
        assert len(history) == 1
        assert history[0].sha == v.sha
        tag = s2.resolve_tag("classifier", "production")
        assert tag is not None and tag.sha == v.sha
        s2.close()
    finally:
        if path.exists():
            path.unlink()


def test_names_lists_all_prompt_names() -> None:
    s = PromptStore(":memory:")
    s.commit("alpha", "a")
    s.commit("beta", "b")
    s.commit("alpha", "a2")  # same name, different body
    assert s.names() == ["alpha", "beta"]
