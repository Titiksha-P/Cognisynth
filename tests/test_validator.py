"""Offline regression tests for the deterministic safety gate."""
import unittest
from safety.formal_validator import SAFE_FALLBACK, UIBlueprint, validate_proposal


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


if __name__ == "__main__":
    unittest.main()
