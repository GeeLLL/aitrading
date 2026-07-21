from __future__ import annotations

import unittest

from execution.ai_signal_contract import AiDirection, parse_ai_signal


class AiSignalContractTests(unittest.TestCase):
    def test_valid_bounded_signal(self) -> None:
        signal = parse_ai_signal({
            "model_version": "model-1", "prompt_version": "prompt-1",
            "candidate": "SPY", "direction": "NO_TRADE", "confidence": "0.4",
            "regime": "MIXED", "event_risk": "LOW", "abstain": True,
            "reason_codes": ["INSUFFICIENT_EDGE"],
        })
        self.assertEqual(AiDirection.NO_TRADE, signal.direction)

    def test_extra_fields_and_contradictory_abstention_reject(self) -> None:
        value = {
            "model_version": "m", "prompt_version": "p", "candidate": "SPY",
            "direction": "CALL", "confidence": 0.5, "regime": "UP",
            "event_risk": "LOW", "abstain": True, "reason_codes": [],
        }
        with self.assertRaises(ValueError):
            parse_ai_signal(value)
