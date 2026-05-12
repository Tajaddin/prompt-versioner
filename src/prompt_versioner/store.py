"""SQLite-backed prompt versioning.

Every commit creates an immutable :class:`PromptVersion` row keyed on a
``(name, sha)`` pair. Multiple "names" can coexist in the same store (think
of them as separate files in a git repo) and each name has its own linear
history. Tags (``production``, ``staging``, named release tags) point at one
``sha`` per name.

The implementation is intentionally small and synchronous — CLI use is one-
shot, library use is from a single thread.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


def _sha(text: str) -> str:
    """Short 12-hex content-addressed id."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


@dataclass
class PromptVersion:
    name: str
    sha: str
    body: str
    message: str
    author: str
    created_at: float
    meta: dict


@dataclass
class Tag:
    name: str
    target_sha: str
    label: str
    updated_at: float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_versions (
    name        TEXT NOT NULL,
    sha         TEXT NOT NULL,
    body        TEXT NOT NULL,
    message     TEXT NOT NULL DEFAULT '',
    author      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    meta_json   TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (name, sha)
);
CREATE INDEX IF NOT EXISTS prompt_versions_name_idx ON prompt_versions(name, created_at);

CREATE TABLE IF NOT EXISTS prompt_tags (
    name        TEXT NOT NULL,
    label       TEXT NOT NULL,
    target_sha  TEXT NOT NULL,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (name, label)
);

CREATE TABLE IF NOT EXISTS eval_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    sha            TEXT NOT NULL,
    eval_set       TEXT NOT NULL,
    pass_count     INTEGER NOT NULL,
    total_count    INTEGER NOT NULL,
    pass_rate      REAL NOT NULL,
    notes          TEXT,
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS eval_results_lookup_idx ON eval_results(name, sha, eval_set, created_at);
"""


class PromptStore:
    """SQLite-backed prompt history.

    Path may be ``":memory:"`` (default) or any file path. Multiple commits with
    the same body produce the same ``sha`` and are deduped via INSERT OR IGNORE
    so re-committing an unchanged prompt is a no-op (like git's content
    addressing).
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = ":memory:" if path == ":memory:" or path is None else str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # commits
    # ------------------------------------------------------------------
    def commit(
        self,
        name: str,
        body: str,
        *,
        message: str = "",
        author: str = "",
        meta: dict | None = None,
    ) -> PromptVersion:
        if not name:
            raise ValueError("name must be non-empty")
        sha = _sha(body)
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO prompt_versions (name, sha, body, message, author, created_at, meta_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, sha, body, message, author, now, meta_json),
            )
        return PromptVersion(
            name=name, sha=sha, body=body, message=message, author=author, created_at=now, meta=meta or {}
        )

    def get(self, name: str, sha: str) -> PromptVersion | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT name, sha, body, message, author, created_at, meta_json FROM prompt_versions WHERE name = ? AND sha = ?",
                (name, sha),
            ).fetchone()
        return self._row_to_version(row) if row else None

    def history(self, name: str, *, limit: int = 50) -> list[PromptVersion]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, sha, body, message, author, created_at, meta_json FROM prompt_versions WHERE name = ? ORDER BY created_at DESC LIMIT ?",
                (name, limit),
            ).fetchall()
        return [self._row_to_version(r) for r in rows]

    def names(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT name FROM prompt_versions ORDER BY name"
            ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # tags
    # ------------------------------------------------------------------
    def tag(self, name: str, label: str, sha: str) -> Tag:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM prompt_versions WHERE name = ? AND sha = ?", (name, sha)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown (name, sha): ({name!r}, {sha!r})")
            now = time.time()
            self._conn.execute(
                "INSERT INTO prompt_tags (name, label, target_sha, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(name, label) DO UPDATE SET target_sha = excluded.target_sha, updated_at = excluded.updated_at",
                (name, label, sha, now),
            )
        return Tag(name=name, target_sha=sha, label=label, updated_at=now)

    def resolve_tag(self, name: str, label: str) -> PromptVersion | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT target_sha FROM prompt_tags WHERE name = ? AND label = ?",
                (name, label),
            ).fetchone()
        if row is None:
            return None
        return self.get(name, row[0])

    def tags(self, name: str) -> list[Tag]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, label, target_sha, updated_at FROM prompt_tags WHERE name = ? ORDER BY updated_at DESC",
                (name,),
            ).fetchall()
        return [Tag(name=r[0], label=r[1], target_sha=r[2], updated_at=r[3]) for r in rows]

    # ------------------------------------------------------------------
    # eval results — durable record of how a version scored on a saved set
    # ------------------------------------------------------------------
    def record_eval(
        self,
        name: str,
        sha: str,
        eval_set: str,
        pass_count: int,
        total_count: int,
        notes: str | None = None,
    ) -> None:
        if total_count < 0:
            raise ValueError("total_count must be >= 0")
        if pass_count < 0 or pass_count > total_count:
            raise ValueError("pass_count out of range")
        pass_rate = (pass_count / total_count) if total_count else 0.0
        with self._lock:
            self._conn.execute(
                "INSERT INTO eval_results (name, sha, eval_set, pass_count, total_count, pass_rate, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, sha, eval_set, pass_count, total_count, pass_rate, notes, time.time()),
            )

    def latest_eval(self, name: str, sha: str, eval_set: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT pass_count, total_count, pass_rate, notes, created_at FROM eval_results "
                "WHERE name = ? AND sha = ? AND eval_set = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (name, sha, eval_set),
            ).fetchone()
        if row is None:
            return None
        return {
            "pass_count": row[0],
            "total_count": row[1],
            "pass_rate": row[2],
            "notes": row[3],
            "created_at": row[4],
        }

    # ------------------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row_to_version(row: tuple) -> PromptVersion:
        name, sha, body, message, author, created_at, meta_json = row
        return PromptVersion(
            name=name,
            sha=sha,
            body=body,
            message=message,
            author=author,
            created_at=float(created_at),
            meta=json.loads(meta_json) if meta_json else {},
        )
