"""Offline regression tests for the deterministic safety gate."""
import json
import unittest

from safety.formal_validator import (
    SAFE_FALLBACK,
    UIBlueprint,
    validate_payload,
    validate_proposal,
)


class ValidatorTests(unittest.TestCase):
    def test_safe_fallback_is_accepted(self):
        result = validate_proposal(SAFE_FALLBACK, expected_panic_index=0.0)
        self.assertTrue(result.accepted, result.violations)

    def test_hidden_critical_control_falls_back(self):
        proposal = SAFE_FALLBACK.model_copy(deep=True)
        proposal.visible_controls.remove("master_emergency_shutoff")
        result = validate_proposal(proposal, expected_panic_index=0.0)
        self.assertFalse(result.accepted)
        self.assertIn("CRITICAL_CONTROL_HIDDEN", {v.code for v in result.violations})

    def test_render_budget_has_temporal_diagnostic(self):
        proposal = SAFE_FALLBACK.model_copy(deep=True)
        proposal.render_budget_ms = 20.0
        result = validate_proposal(proposal, expected_panic_index=0.0)
        self.assertFalse(result.accepted)
        self.assertIn("FRAME_BUDGET_EXCEEDED", {v.code for v in result.violations})

    def _fallback_payload(self):
        return SAFE_FALLBACK.model_dump()

    def test_corrupt_json_hidden_critical_control_falls_back(self):
        payload = self._fallback_payload()
        payload["visible_controls"].remove("master_emergency_shutoff")

        result = validate_payload(json.dumps(payload), expected_panic_index=0.0)

        self.assertFalse(result.accepted)
        self.assertEqual(result.blueprint, SAFE_FALLBACK)
        self.assertIn("CRITICAL_CONTROL_HIDDEN", {v.code for v in result.violations})

    def test_corrupt_json_graph_without_path_to_home_falls_back(self):
        payload = self._fallback_payload()
        payload["navigation_graph"]["orphan_node"] = ["unreachable_node"]

        result = validate_payload(json.dumps(payload), expected_panic_index=0.0)

        self.assertFalse(result.accepted)
        self.assertEqual(result.blueprint, SAFE_FALLBACK)
        self.assertIn("SCHEMA_VALIDATION_ERROR", {v.code for v in result.violations})
        with self.assertRaises(Exception):
            UIBlueprint.model_validate_json(json.dumps(payload))

    def test_corrupt_json_density_ceiling_violation_falls_back(self):
        payload = self._fallback_payload()
        payload["density_score"] = 9.0

        result = validate_payload(json.dumps(payload), expected_panic_index=0.0)

        self.assertFalse(result.accepted)
        self.assertEqual(result.blueprint, SAFE_FALLBACK)
        self.assertIn("DENSITY_CEILING_EXCEEDED", {v.code for v in result.violations})

    def test_corrupt_json_temporal_ceiling_violation_falls_back(self):
        payload = self._fallback_payload()
        payload["render_budget_ms"] = 20.0

        result = validate_payload(json.dumps(payload), expected_panic_index=0.0)

        self.assertFalse(result.accepted)
        self.assertEqual(result.blueprint, SAFE_FALLBACK)
        self.assertIn("FRAME_BUDGET_EXCEEDED", {v.code for v in result.violations})


if __name__ == "__main__":
    unittest.main()
