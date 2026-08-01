from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from strategy.policy_labels import PolicyLabelError, load_policy_labels


class PolicyLabelRegistryTests(unittest.TestCase):
    def test_repo_registry_loads_and_freezes_current_labels(self) -> None:
        registry = load_policy_labels(".")
        self.assertEqual({"BASE_18", "BASE_21"}, set(registry.policy))
        self.assertIn("NEAR_MISS", registry.research)
        # Retired labels stay classifiable so historical trajectories keep
        # counting as policy-of-their-era.
        for retired in ("BASE_25", "BASE_30", "AI_RANK_V1"):
            self.assertIn(retired, registry.policy_for_classification)
        self.assertEqual(1.8, registry.thresholds["BASE_18"])
        self.assertEqual(2.1, registry.thresholds["BASE_21"])

    def test_labels_for_volume_ratio(self) -> None:
        registry = load_policy_labels(".")
        self.assertEqual((), registry.labels_for_volume_ratio(1.5))
        self.assertEqual(("BASE_18",), registry.labels_for_volume_ratio(1.9))
        self.assertEqual(("BASE_18", "BASE_21"), registry.labels_for_volume_ratio(2.5))

    def test_missing_registry_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(PolicyLabelError, "UNREADABLE"):
                load_policy_labels(directory)

    def test_overlapping_sets_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            config.mkdir()
            (config / "policy_labels.toml").write_text(
                'schema_version = 1\nversion = "v1"\n'
                "[policy.X]\nmin_volume_ratio = 1.0\n"
                "[research.X]\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PolicyLabelError, "OVERLAP"):
                load_policy_labels(directory)

    def test_empty_policy_set_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            config.mkdir()
            (config / "policy_labels.toml").write_text(
                'schema_version = 1\nversion = "v1"\n[research.NEAR_MISS]\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PolicyLabelError, "EMPTY_POLICY_SET"):
                load_policy_labels(directory)


if __name__ == "__main__":
    unittest.main()
