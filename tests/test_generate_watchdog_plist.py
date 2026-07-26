from __future__ import annotations

import unittest
from pathlib import Path

from scripts.generate_watchdog_plist import render


class GenerateWatchdogPlistTests(unittest.TestCase):
    def test_paths_follow_the_actual_repo_and_interpreter(self) -> None:
        # A path that exists on no machine: resolve() then only normalizes it,
        # so the assertion is not sensitive to local symlinks (e.g. Homebrew's
        # python3 -> Cellar/python@3.x on real Macs). Same trick as the sibling
        # test_generate_plist.py — a real path here fails on any Mac with
        # Homebrew Python installed.
        python = Path("/nonexistent-test-prefix/bin/python3")
        out = render(
            python=python,
            workdir=Path("/Users/ge/ge/aitrading"),
        )
        self.assertIn(f"<string>{python}</string>", out)
        self.assertIn("/Users/ge/ge/aitrading/scripts/watchdog_tick.py", out)
        self.assertNotIn("Documents/AI trading agent", out)

    def test_runs_on_a_60_second_interval_not_a_calendar_date(self) -> None:
        out = render(workdir=Path("/Users/ge/ge/aitrading"))
        self.assertIn("<key>StartInterval</key>", out)
        self.assertIn("<integer>60</integer>", out)
        self.assertNotIn("StartCalendarInterval", out)
        self.assertIn("<key>PATH</key>", out)


if __name__ == "__main__":
    unittest.main()
