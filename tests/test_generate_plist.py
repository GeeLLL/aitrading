from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from monitoring.daily_schedule import DAILY_SLOTS
from scripts.generate_shadow_worker_plist import render


class GeneratePlistTests(unittest.TestCase):
    def test_paths_follow_the_actual_repo_and_interpreter(self) -> None:
        # A path that exists on no machine: resolve() then only normalizes it,
        # so the assertion is not sensitive to local symlinks (e.g. Homebrew's
        # python3 -> Cellar/... on some Macs).
        python = Path("/nonexistent-test-prefix/bin/python3")
        out = render(
            date(2026, 7, 21),
            python=python,
            workdir=Path("/Users/ge/ge/aitrading"),
        )
        self.assertIn(f"<string>{python}</string>", out)
        self.assertIn("/Users/ge/ge/aitrading/scripts/launchd_shadow_worker.py", out)
        self.assertNotIn("Documents/AI trading agent", out)
        self.assertIn("<key>PATH</key>", out)

    def test_all_slots_pinned_to_the_requested_single_date(self) -> None:
        out = render(date(2026, 7, 21), workdir=Path("/Users/ge/ge/aitrading"))
        self.assertEqual(len(DAILY_SLOTS), out.count("<key>Day</key><integer>21</integer>"))
        self.assertNotIn("<integer>22</integer>", out)


if __name__ == "__main__":
    unittest.main()
