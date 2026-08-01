from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _dispatch_targets(tree: ast.Module) -> set[str]:
    """Every function name main() dispatches to via `return <name>(...)`."""
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "main":
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Return)
                and isinstance(inner.value, ast.Call)
                and isinstance(inner.value.func, ast.Name)
            ):
                targets.add(inner.value.func.id)
    return targets


class CliDispatchIntegrityTests(unittest.TestCase):
    """A CLI subcommand whose handler does not exist fails only when a user
    runs it — which is how `cost-hurdle` and `validation-power` were silently
    deleted by an unrelated edit and stayed broken until someone tried them.
    These tests make that class of breakage fail in CI instead."""

    def setUp(self) -> None:
        self.source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.defined = {
            node.name for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
        }

    def test_every_dispatched_handler_is_defined(self) -> None:
        missing = sorted(_dispatch_targets(self.tree) - self.defined)
        self.assertEqual(missing, [], f"main() dispatches to undefined handlers: {missing}")

    def test_every_registered_subcommand_runs_its_help(self) -> None:
        # --help exercises argparse registration end to end; a missing handler
        # is caught by the dispatch test above, and a broken parser here.
        commands = sorted({
            node.value
            for call in ast.walk(self.tree)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "add_parser"
            for node in call.args[:1]
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        })
        self.assertGreater(len(commands), 15, "subcommand discovery found suspiciously few")
        for command in commands:
            with self.subTest(command=command):
                completed = subprocess.run(
                    [sys.executable, "main.py", command, "--help"],
                    cwd=ROOT, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr[-400:])

    def test_the_research_commands_added_this_week_are_present(self) -> None:
        for command in (
            "cost-hurdle", "validation-power", "evaluate-universe",
            "bar-time-verify", "fresh-quote-probe", "market-check-verify",
        ):
            self.assertIn(f'"{command}"', self.source, f"{command} lost from the CLI")


if __name__ == "__main__":
    unittest.main()
