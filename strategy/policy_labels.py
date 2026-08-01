"""Loader for the frozen policy-label registry (config/policy_labels.toml).

Every component that names a policy label — the deterministic pilot pipeline
that records trajectories, and the EOD report that classifies them — must load
the sets from here. Fail-closed: a missing or malformed registry raises rather
than silently classifying everything as research.
"""

from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11 fallback
    import tomli as tomllib

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class PolicyLabelError(ValueError):
    pass


@dataclass(frozen=True)
class PolicyLabels:
    version: str
    policy: frozenset[str]
    research: frozenset[str]
    retired_policy: frozenset[str]
    thresholds: Mapping[str, float]  # label -> min_volume_ratio where defined

    @property
    def policy_for_classification(self) -> frozenset[str]:
        """Current + retired policy labels: a BASE_25 trajectory from July was a
        policy trade under that era's registry and must keep classifying so."""
        return self.policy | self.retired_policy

    def labels_for_volume_ratio(self, volume_ratio: float) -> tuple[str, ...]:
        """Current policy labels whose threshold this volume ratio clears."""
        cleared = [
            label for label in sorted(self.policy)
            if label in self.thresholds and volume_ratio >= self.thresholds[label]
        ]
        return tuple(cleared)


def load_policy_labels(project_root: str | Path = ".") -> PolicyLabels:
    path = Path(project_root) / "config/policy_labels.toml"
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PolicyLabelError(f"POLICY_LABELS_UNREADABLE:{path}") from error
    if raw.get("schema_version") != 1 or not str(raw.get("version") or "").strip():
        raise PolicyLabelError("POLICY_LABELS_SCHEMA_INVALID")

    def _section(name: str) -> dict[str, dict]:
        section = raw.get(name) or {}
        if not isinstance(section, dict):
            raise PolicyLabelError(f"POLICY_LABELS_SECTION_INVALID:{name}")
        return section

    policy = _section("policy")
    research = _section("research")
    retired = _section("retired_policy")
    if not policy:
        raise PolicyLabelError("POLICY_LABELS_EMPTY_POLICY_SET")
    overlap = set(policy) & (set(research) | set(retired))
    if overlap:
        raise PolicyLabelError(f"POLICY_LABELS_OVERLAP:{sorted(overlap)}")

    thresholds: dict[str, float] = {}
    for section in (policy, retired):
        for label, body in section.items():
            if isinstance(body, dict) and isinstance(body.get("min_volume_ratio"), (int, float)):
                thresholds[label] = float(body["min_volume_ratio"])

    return PolicyLabels(
        version=str(raw["version"]),
        policy=frozenset(policy),
        research=frozenset(research),
        retired_policy=frozenset(retired),
        thresholds=thresholds,
    )
