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

    def malformed_confidence(self, confidence) -> dict:
        return {
            "model_version": "m", "prompt_version": "p", "candidate": "SPY",
            "direction": "CALL", "confidence": confidence, "regime": "UP",
            "event_risk": "LOW", "abstain": False, "reason_codes": [],
        }

    def test_every_malformed_confidence_raises_value_error(self) -> None:
        # The documented contract is ValueError -> NO_TRADE upstream; a stray
        # decimal.InvalidOperation would escape that handler.
        for confidence in ("abc", "nan", "NaN", "inf", "-inf", True, None, [], {}):
            with self.assertRaises(ValueError, msg=repr(confidence)):
                parse_ai_signal(self.malformed_confidence(confidence))

    def test_unknown_direction_raises_value_error(self) -> None:
        value = self.malformed_confidence("0.5") | {"direction": "STRADDLE"}
        with self.assertRaises(ValueError):
            parse_ai_signal(value)
