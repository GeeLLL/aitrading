from __future__ import annotations

import sys
import unittest
from pathlib import Path

from scripts.generate_preflight_plist import render


class GeneratePreflightPlistTests(unittest.TestCase):
    def test_paths_follow_the_actual_repo_and_interpreter(self) -> None:
        python_exe = Path(sys.executable).resolve()
        out = render(python=python_exe, workdir=Path("/Users/ge/ge/aitrading"))
        self.assertIn(f"<string>{python_exe}</string>", out)
        self.assertIn("/Users/ge/ge/aitrading/scripts/preflight_check.py", out)
        self.assertNotIn("Documents/AI trading agent", out)

    def test_runs_weekdays_at_0545_and_not_at_load(self) -> None:
        out = render(workdir=Path("/Users/ge/ge/aitrading"))
        self.assertIn("<key>RunAtLoad</key>", out)
        self.assertIn("<false/>", out)
        self.assertEqual(out.count("<key>Weekday</key>"), 5)
        self.assertIn("<integer>5</integer>", out)   # hour
        self.assertIn("<integer>45</integer>", out)  # minute
        self.assertNotIn("StartInterval", out)
        self.assertIn("<key>PATH</key>", out)


if __name__ == "__main__":
    unittest.main()
