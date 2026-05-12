"""Deterministic A/B traffic splitter.

Given a list of ``(sha, weight)`` arms, ``choose(arm_key)`` returns the
selected sha. The decision is a hash of ``arm_key`` so the same input always
lands on the same arm — useful for sticky-user A/B tests across requests.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class TrafficSplit:
    arms: tuple[tuple[str, float], ...]   # ((sha, weight), ...) — weights need not sum to 1

    def __post_init__(self) -> None:
        if not self.arms:
            raise ValueError("TrafficSplit requires at least one arm")
        for sha, w in self.arms:
            if not sha:
                raise ValueError("arm sha must be non-empty")
            if w < 0:
                raise ValueError("arm weights must be non-negative")
        total = sum(w for _, w in self.arms)
        if total <= 0:
            raise ValueError("total weight must be > 0")

    @property
    def total_weight(self) -> float:
        return sum(w for _, w in self.arms)

    def normalized(self) -> list[tuple[str, float]]:
        t = self.total_weight
        return [(s, w / t) for s, w in self.arms]


def _hash_to_unit_interval(key: str) -> float:
    """Deterministic [0, 1) hash."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    # First 8 bytes → big-endian unsigned int, mapped to [0, 1).
    n = int.from_bytes(digest[:8], "big")
    return n / 2**64


def choose(split: TrafficSplit, arm_key: str) -> str:
    """Return the sha selected for ``arm_key``."""
    u = _hash_to_unit_interval(arm_key)
    cumulative = 0.0
    weights = split.normalized()
    for sha, w in weights:
        cumulative += w
        if u < cumulative:
            return sha
    # Floating-point corner: u very close to 1.0 → fall back to the last arm.
    return weights[-1][0]
