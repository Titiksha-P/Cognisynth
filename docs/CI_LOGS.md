# Cognisynth CI Validation Logs

## Purpose

This document defines the evidence emitted by the Cognisynth automated test pipeline. It distinguishes deterministic software-contract checks from measurements that require a real browser, instrumented runtime, or human-subject study.

## Validation trace

A successful CI run should establish all of the following for the code and tests executed by that workflow:

1. **Python test discovery and execution** — the existing `test_*.py` suite runs under `unittest`; non-zero test failures fail the job.
2. **Pydantic schema contract** — valid `UIBlueprint` payloads parse, while malformed or structurally invalid payloads are rejected and `validate_payload()` returns a `ValidationResult` selecting `SAFE_FALLBACK`.
3. **Navigation graph return paths** — `_nodes_reach_home()` rejects a graph with a missing operational home or any declared/referenced node that has no directed path back to `home`.
4. **Critical controls** — the deterministic validator checks that `master_emergency_shutoff`, `alarm_acknowledge`, and `manual_override` remain visible and are reachable from operational home.
5. **Declared render budget** — the validator rejects `render_budget_ms > 16.67 ms`, corresponding to the nominal 60 Hz frame interval \(1000/60 \approx 16.67\) ms.
6. **Panic-dependent density ceiling** — candidates exceeding `3.0 − 1.5 × clip(panic_index, 0, 1)` are rejected.
7. **Cross-window visual entropy** — when the caller supplies the previous accepted panic index and visual entropy, increasing stress cannot increase the entropy proxy beyond the configured tolerance. The default tolerance is zero.
8. **Fail-closed timeout behavior** — the compiler returns the fallback when its caller-side inference wait expires. A synchronous worker thread may continue in the background until its underlying operation ends; Python does not forcibly terminate it.

## Example successful local verification trace

The following is an illustrative format, not a transcript of a specific GitHub Actions run:

```text
COGNISYNTH LOCAL PIPELINE VERIFICATION
Declared render budget ceiling: 16.67 ms

=== Focused invariant checks (unittest) ===
test_baseline_valid_invariant_state ... ok
test_stateful_monotonicity_violation_fails_closed ... ok
test_structural_control_violation_fails_closed ... ok
Ran 3 tests
OK

=== Existing repository unittest suite (captured output) ===
Repository unittest suite completed successfully; the runner reports the actual test count.

✅ PIPELINE INVARIANTS STATUS: SUCCESSFUL
```

The actual GitHub Actions conclusion must be taken from the executed run, not inferred from this example.

## Scope and interpretation

A passing unit-test workflow verifies the assertions covered by those tests on the tested software revision. It does **not** by itself prove:

- that browser rendering or DOM mutation completes within 16.67 ms;
- a hard real-time guarantee under operating-system scheduling, garbage collection, or network failure;
- that the visual entropy proxy is a validated cognitive-load measure;
- human-factors effectiveness, operational safety, or certification.

The `render_budget_ms` check enforces a declared candidate budget. Demonstrating actual frame-time compliance requires browser instrumentation and representative load testing, including the validation path, DOM mutation, layout, paint, and scheduling. Synthetic telemetry is suitable for deterministic regression testing, not as a substitute for empirical operator data.

## Local command

From the repository root:

```bash
python -m pip install -r requirements.txt
python tests/verify_pipeline.py
```

The verification script prints the focused invariant-test output, captures the repository's existing `unittest` output, and exits non-zero if either suite fails.
