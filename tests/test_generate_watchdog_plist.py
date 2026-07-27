from __future__ import annotations

import sys
import unittest
from pathlib import Path

from scripts.generate_watchdog_plist import render


class GenerateWatchdogPlistTests(unittest.TestCase):
    def test_paths_follow_the_actual_repo_and_interpreter(self) -> None:
        # Use the actual Python executable path, which gets resolved to its real location
        python_exe = Path(sys.executable).resolve()
        out = render(
            python=python_exe,
            workdir=Path("/Users/ge/ge/aitrading"),
        )
        # Verify the actual resolved Python path appears in the output
        self.assertIn(f"<string>{python_exe}</string>", out)
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
