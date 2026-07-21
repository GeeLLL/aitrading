#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.fault_injection import expected_fault_matrix, qualify_fault_matrix
from monitoring.shadow_readiness import build_shadow_readiness
from research.parameter_audit import audit_parameters


def main() -> int:
    readiness = build_shadow_readiness(root=ROOT)
    raw = json.loads((ROOT / "config/parameter_evidence.example.json").read_text(encoding="utf-8"))
    parameter_inventory = audit_parameters(raw["parameters"], raw["evidence_versions"])
    faults = expected_fault_matrix()
    fault_safe, fault_reasons = qualify_fault_matrix(faults)
    report = {
        "schema_version": 1,
        "mode": "READ_ONLY_OFFHOURS_QUALIFICATION",
        "offline_ready": readiness.offline_ready,
        "formal_shadow_authorized": readiness.formal_shadow_authorized,
        "parameter_inventory_complete": parameter_inventory.complete,
        "parameter_evidence_status": raw["status"],
        "fault_scenarios_defined": len(faults),
        "fault_matrix_safe": fault_safe,
        "fault_matrix_reasons": list(fault_reasons),
        "live_mode_blocked": True,
        "pending_market_checks": list(readiness.pending_market_checks),
        "note": "Local qualification is not market evidence or a profitability result.",
    }
    destination = ROOT / "logs" / "qualification" / "latest.offhours.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if readiness.offline_ready and parameter_inventory.complete and fault_safe else 1


if __name__ == "__main__":
    raise SystemExit(main())
