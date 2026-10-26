#!/usr/bin/env python3
"""Local Cognisynth pipeline verification harness.

Run from the repository root:
    python tests/verify_pipeline.py

The harness runs three focused contract checks, then invokes the repository's
existing unittest suite in a subprocess and captures both output streams.
This verifies declared render-budget policy; it does not measure browser frame time.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.cognitive_compiler import TelemetrySnapshot, compile_blueprint, panic_index
from safety.formal_validator import (
    FRAME_BUDGET_MS,
    OPERATIONAL_HOME,
    SAFE_FALLBACK,
    UIBlueprint,
    _nodes_reach_home,
    validate_payload,
    validate_proposal,
    visual_entropy_index,
)


class OfflineStructuredOutputClient:
    """Deterministic fake transport with the response shape expected by the compiler."""

    def __init__(self, blueprint: UIBlueprint | None = None) -> None:
        self.blueprint = (blueprint or SAFE_FALLBACK).model_copy(deep=True)
        self.beta = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(parse=self.parse)
            )
        )

    def parse(self, **kwargs):
        if kwargs.get("response_format") is not UIBlueprint:
            raise AssertionError("Compiler requested an unexpected structured-output schema")
        candidate = self.blueprint.model_copy(deep=True)
        prompt = next(
            (str(message.get("content", "")) for message in kwargs.get("messages", [])
             if message.get("role") == "user"),
            "",
        )
        # compile_blueprint includes its deterministic estimate in the user prompt.
        import re
        match = re.search(r"panic_index=([0-9]*\.?[0-9]+)", prompt)
        if match:
            candidate.panic_index = min(1.0, max(0.0, float(match.group(1))))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=candidate))]
        )


def make_telemetry() -> TelemetrySnapshot:
    return TelemetrySnapshot(
        timestamp=__import__("time").time(),
        heart_rate_bpm=72.0,
        error_rate=0.05,
        response_latency_ms=250.0,
        alarm_rate=0.5,
        workload_self_report=1.5,
    )


class PipelineInvariantTests(unittest.TestCase):
    """Focused checks for the compiler, schema gate, graph invariant, and temporal gate."""

    def test_baseline_valid_invariant_state(self) -> None:
        telemetry = make_telemetry()
        expected_panic = panic_index(telemetry)

        compiled = compile_blueprint(
            telemetry,
            client=OfflineStructuredOutputClient(),
            timeout_seconds=0.75,
            previous_blueprint=SAFE_FALLBACK,
        )
        self.assertIsInstance(compiled, UIBlueprint)
        self.assertLessEqual(compiled.render_budget_ms, FRAME_BUDGET_MS)

        result = validate_payload(
            compiled.model_dump_json(),
            expected_panic_index=expected_panic,
            previous_panic_index=0.0,
            previous_visual_entropy_index=visual_entropy_index(SAFE_FALLBACK),
        )
        self.assertTrue(result.accepted, [v.code for v in result.violations])
        self.assertEqual(result.violations, [])
        self.assertTrue(_nodes_reach_home(result.blueprint.navigation_graph)[0])

        # Also exercise the direct proposal interface on the same low-complexity state.
        direct = validate_proposal(
            compiled,
            expected_panic_index=expected_panic,
            previous_panic_index=0.0,
            previous_visual_entropy_index=visual_entropy_index(SAFE_FALLBACK),
        )
        self.assertTrue(direct.accepted, [v.code for v in direct.violations])

    def test_structural_control_violation_fails_closed(self) -> None:
        payload = SAFE_FALLBACK.model_dump(mode="json")
        payload["navigation_graph"]["orphaned_panel"] = ["unreachable_destination"]

        graph_ok, graph_message = _nodes_reach_home(payload["navigation_graph"])
        self.assertFalse(graph_ok)
        self.assertIn("unreachable", graph_message.lower())

        result = validate_payload(json.dumps(payload), expected_panic_index=0.0)
        self.assertFalse(result.accepted)
        self.assertEqual(result.blueprint, SAFE_FALLBACK)
        self.assertIn("SCHEMA_VALIDATION_ERROR", {item.code for item in result.violations})

    def test_stateful_monotonicity_violation_fails_closed(self) -> None:
        previous_entropy = visual_entropy_index(SAFE_FALLBACK)
        candidate = SAFE_FALLBACK.model_copy(deep=True)
        candidate.visible_streams.extend(f"synthetic_stream_{i}" for i in range(24))
        candidate.panic_index = 0.60
        candidate.density_score = 1.5

        self.assertGreater(visual_entropy_index(candidate), previous_entropy)
        result = validate_proposal(
            candidate,
            expected_panic_index=0.60,
            previous_panic_index=0.20,
            previous_visual_entropy_index=previous_entropy,
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.blueprint, SAFE_FALLBACK)
        self.assertIn(
            "VISUAL_ENTROPY_MONOTONICITY_VIOLATION",
            {item.code for item in result.violations},
        )


def run_focused_checks() -> tuple[bool, str]:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PipelineInvariantTests)
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    return result.wasSuccessful(), output.getvalue()


def run_repository_suite() -> tuple[bool, str]:
    """Run the established test_*.py suite and capture stdout and stderr."""
    command = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        str(REPO_ROOT / "tests"),
        "-p",
        "test_*.py",
        "-v",
    ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return completed.returncode == 0, f"$ {' '.join(command)}\n{combined}"


def main() -> int:
    print("COGNISYNTH LOCAL PIPELINE VERIFICATION")
    print(f"Repository root: {REPO_ROOT}")
    print(f"Declared render budget ceiling: {FRAME_BUDGET_MS:.2f} ms")
    print("Note: declared-budget validation is not a measurement of actual browser render time.")
    print()

    focused_ok, focused_output = run_focused_checks()
    print("=== Focused invariant checks (unittest) ===")
    print(focused_output.rstrip())
    if not focused_ok:
        print("\n❌ PIPELINE INVARIANTS STATUS: FAILED")
        return 1

    repository_ok, repository_output = run_repository_suite()
    print("\n=== Existing repository unittest suite (captured output) ===")
    print(repository_output.rstrip())
    if not repository_ok:
        print("\n❌ PIPELINE INVARIANTS STATUS: FAILED")
        return 1

    print("\n✅ PIPELINE INVARIANTS STATUS: SUCCESSFUL")
    print("Focused invariant checks and the existing unittest suite both passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
