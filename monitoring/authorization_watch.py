"""Detect any change to the formal-Shadow authorization record.

Threat model, stated plainly: the unattended pilot agent runs with
``Bash(python3:*)`` in its allowlist, which is arbitrary code execution under
the owner's account. No in-process guard — deny rules, file permissions, even a
secret — can *prevent* such an agent from writing
``state/shadow_authorization.json``, because anything the checking code can
read, the agent can read too.

What is achievable is that forgery cannot happen QUIETLY. This module records a
baseline fingerprint of the authorization record and files a CRITICAL incident
(plus the watchdog's alert path) whenever the record appears, changes, or
disappears without the owner acknowledging it. The owner knows when they typed
the confirmation phrase; an alert they did not expect is unambiguous evidence.

Acknowledgement is the existing sanctioned flow: resolve the incident, then
re-baseline with ``acknowledge_authorization``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

AUTHORIZATION_PATH = "state/shadow_authorization.json"
BASELINE_PATH = "logs/scheduler/authorization_baseline.json"

# The kill switch is armed-by-absence: creating state/trading_armed DISARMS it.
# That marker is reachable by the same arbitrary-python3 path, so it is watched
# with the same machinery — a marker appearing without the owner creating it is
# an emergency, and must never be quiet.
GOVERNED_PATHS = {
    "shadow_authorization": AUTHORIZATION_PATH,
    "trading_arm_marker": "state/trading_armed",
    "automation_halt": "state/automation_halt.json",
}

ABSENT = "ABSENT"


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def fingerprint(path: Path) -> str:
    """sha256 of the authorization record, or ABSENT when it does not exist."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ABSENT


def read_baseline(path: Path) -> dict[str, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    prints = payload.get("fingerprints")
    if isinstance(prints, dict) and all(isinstance(v, str) for v in prints.values()):
        return {str(k): str(v) for k, v in prints.items()}
    legacy = payload.get("fingerprint")
    if isinstance(legacy, str):  # schema_version 1
        return {"shadow_authorization": legacy}
    return None


def current_fingerprints(root: Path) -> dict[str, str]:
    return {name: fingerprint(root / rel) for name, rel in sorted(GOVERNED_PATHS.items())}


def acknowledge_authorization(
    *,
    project_root: Path,
    acknowledged_by: str,
    note: str = "",
) -> str:
    """Record the current governed-file fingerprints as owner-acknowledged."""
    root = Path(project_root)
    prints = current_fingerprints(root)
    _atomic_json(root / BASELINE_PATH, {
        "schema_version": 2,
        "fingerprint": prints["shadow_authorization"],  # back-compat field
        "fingerprints": prints,
        "acknowledged_at": datetime.now(timezone.utc).isoformat(),
        "acknowledged_by": acknowledged_by,
        "note": note,
    })
    return prints["shadow_authorization"]


def check_authorization_record(
    now: datetime,
    *,
    project_root: Path,
    incident_dir: Path | None = None,
) -> dict[str, object]:
    """Compare every governed file against its acknowledged baseline.

    First observation self-baselines ONLY when all governed files are absent
    (the normal state today). Any later divergence files an idempotent CRITICAL
    incident per changed file and returns them for alert delivery.
    """
    root = Path(project_root)
    incidents = incident_dir if incident_dir is not None else root / "logs/incidents"
    baseline_path = root / BASELINE_PATH
    observed = current_fingerprints(root)
    baseline = read_baseline(baseline_path)

    if baseline is None:
        if all(value == ABSENT for value in observed.values()):
            _atomic_json(baseline_path, {
                "schema_version": 2,
                "fingerprint": ABSENT,
                "fingerprints": observed,
                "acknowledged_at": now.astimezone(timezone.utc).isoformat(),
                "acknowledged_by": "AUTO_BASELINE_NO_GOVERNED_FILES_PRESENT",
                "note": "No governed file existed when the watch began.",
            })
            return {"status": "BASELINED_ABSENT", "changed": False}
        baseline = {}

    changed: list[str] = []
    paths: list[str] = []
    for name in sorted(GOVERNED_PATHS):
        expected = baseline.get(name, ABSENT)
        actual = observed[name]
        if actual == expected:
            continue
        changed.append(name)
        incident_id = f"governed-file-change-{name}-{actual[:12] if actual != ABSENT else 'removed'}"
        incident_path = incidents / f"{incident_id}.scheduler-incident.json"
        paths.append(str(incident_path))
        if incident_path.exists():
            continue
        _atomic_json(incident_path, {
            "schema_version": 1,
            "incident_type": "GOVERNED_FILE_CHANGED",
            "governed_file": name,
            "run_id": incident_id,
            "detected_at": now.astimezone(timezone.utc).isoformat(),
            "severity": "CRITICAL",
            "requires_owner_review": True,
            "new_entries_blocked": True,
            "catch_up_policy": "OWNER_MUST_CONFIRM_OR_REVOKE",
            "baseline_fingerprint": expected,
            "observed_fingerprint": actual,
            "path": str(root / GOVERNED_PATHS[name]),
            "note": (
                "A governance file changed. If the owner did not just make this "
                "change deliberately, treat it as forgery by an automated "
                "process and revert it immediately."
            ),
        })
        _atomic_json(incidents / "alerts" / f"{incident_id}.alert.json", {
            "schema_version": 1,
            "run_id": incident_id,
            "title": "治理文件被修改",
            "message": f"{GOVERNED_PATHS[name]} 已变化；若非你本人操作，请立即撤销。",
            "incident_path": str(incident_path),
        })

    if not changed:
        return {"status": "UNCHANGED", "changed": False}
    return {
        "status": "CHANGED",
        "changed": True,
        "changed_files": changed,
        "observed": observed,
        "incident_paths": paths,
        "incident_path": paths[0],
    }
