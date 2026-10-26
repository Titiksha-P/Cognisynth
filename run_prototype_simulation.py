#!/usr/bin/env python3
"""Offline, reproducible Cognisynth end-to-end simulation.

Run from the repository root:
    python run_prototype_simulation.py
    python run_prototype_simulation.py --verbose
    python run_prototype_simulation.py --realtime
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Any

from core.cognitive_compiler import TelemetrySnapshot, compile_blueprint, panic_index
from safety.formal_validator import (
    OPERATIONAL_HOME,
    SAFE_FALLBACK,
    UIBlueprint,
    ValidationResult,
    _nodes_reach_home,
    validate_payload,
    validate_proposal,
    visual_entropy_index,
)

DEADLINE_SECONDS = 0.750
LOG = logging.getLogger("cognisynth.simulation")


@dataclass(frozen=True)
class SyntheticFeatures:
    second: int
    mouse_trajectory_fractal_dimension: float
    keystroke_velocity_gradient: float
    alert_acknowledgment_latency_delta_ms: float
    command_repetition_volatility: float


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s.%(msecs)03d | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )


def generate_feature_stream(duration: int = 60, seed: int = 7) -> list[SyntheticFeatures]:
    """Generate synthetic baseline, escalating-stress, and recovery signals."""
    if duration < 1:
        raise ValueError("duration must be >= 1")
    rng = random.Random(seed)
    rows = []
    for second in range(duration):
        phase = second / max(1, duration - 1)
        if phase < 0.20:
            severity = 0.15 + 0.10 * phase / 0.20
        elif phase < 0.70:
            severity = 0.25 + 0.75 * (phase - 0.20) / 0.50
        else:
            severity = max(0.10, 1.0 - 0.90 * (phase - 0.70) / 0.30)
        rows.append(SyntheticFeatures(
            second=second,
            mouse_trajectory_fractal_dimension=round(
                min(2.0, max(1.0, 1.15 + 0.68 * severity + rng.uniform(-0.045, 0.045))), 4),
            keystroke_velocity_gradient=round(
                min(1.0, max(-1.0, -0.08 - 0.78 * severity + rng.uniform(-0.06, 0.06))), 4),
            alert_acknowledgment_latency_delta_ms=round(
                max(50.0, 180.0 + 2700.0 * severity + rng.uniform(-90.0, 90.0)), 2),
            command_repetition_volatility=round(
                min(1.0, max(0.0, 0.04 + 0.88 * severity + rng.uniform(-0.05, 0.05))), 4),
        ))
    return rows


def features_to_telemetry(f: SyntheticFeatures) -> TelemetrySnapshot:
    """Map synthetic features into the repository's established telemetry schema."""
    movement = min(1.0, max(0.0, (f.mouse_trajectory_fractal_dimension - 1.10) / 0.80))
    typing = min(1.0, max(0.0, -f.keystroke_velocity_gradient / 0.90))
    repetition = f.command_repetition_volatility
    return TelemetrySnapshot(
        timestamp=time.time(),
        heart_rate_bpm=min(150.0, 68.0 + 48.0 * movement + 16.0 * repetition),
        error_rate=min(1.0, max(0.0, 0.025 + 0.24 * typing + 0.22 * repetition)),
        response_latency_ms=min(120000.0, max(100.0, 350.0 + f.alert_acknowledgment_latency_delta_ms)),
        alarm_rate=min(100.0, max(0.0, 0.3 + 7.2 * repetition)),
        workload_self_report=min(10.0, max(0.0, 1.0 + 5.5 * movement + 2.5 * typing)),
    )


class OfflineStructuredOutputClient:
    """Fake only the OpenAI response transport; preserve the real compiler pipeline."""
    def __init__(self) -> None:
        self.beta = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(parse=self.parse)))

    @staticmethod
    def parse(**kwargs: Any) -> SimpleNamespace:
        if kwargs.get("response_format") is not UIBlueprint:
            raise TypeError("Expected the repository UIBlueprint response schema")
        candidate = SAFE_FALLBACK.model_copy(deep=True)
        prompt = next((str(m.get("content", "")) for m in kwargs.get("messages", [])
                       if m.get("role") == "user"), "")
        match = re.search(r"panic_index=([0-9]*\.?[0-9]+)", prompt)
        if match:
            candidate.panic_index = min(1.0, max(0.0, float(match.group(1))))
        candidate.rationale = "Offline synthetic candidate; not a real model finding."
        return SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(parsed=candidate))
        ])


def timeout_ceiling_experiment() -> dict[str, Any]:
    """Show caller-side timeout; the running worker is not forcibly terminated."""
    finished = threading.Event()

    def slow_worker() -> dict[str, str]:
        time.sleep(DEADLINE_SECONDS + 0.35)
        finished.set()
        return {"proposal": "late untrusted proposal"}

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cognisynth-timeout-demo")
    future = pool.submit(slow_worker)
    started = time.monotonic()
    try:
        late = future.result(timeout=DEADLINE_SECONDS)
        event = "NO_TIMEOUT"
        accepted = late
    except FuturesTimeoutError:
        event = "TIMEOUT_CEILING_BREACH"
        accepted = None
        future.cancel()  # Cancels pending work only; cannot stop an already-running thread.
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    elapsed_ms = (time.monotonic() - started) * 1000.0
    fallback = SAFE_FALLBACK.model_copy(deep=True)
    LOG.warning(
        "%s | caller_elapsed_ms=%.3f | action=DROP_UNTRUSTED_PROPOSAL "
        "| selected_state=SAFE_FALLBACK | worker_finished_at_return=%s",
        event, elapsed_ms, finished.is_set(),
    )
    return {
        "event": event,
        "caller_elapsed_ms": round(elapsed_ms, 3),
        "accepted_proposal": accepted,
        "selected_blueprint": fallback.model_dump(mode="json"),
        "worker_finished_at_return": finished.is_set(),
    }


def run_simulation(duration: int = 60, seed: int = 7, realtime: bool = False) -> dict[str, Any]:
    LOG.info("SIMULATION_START | duration=%d | seed=%d | deadline_ms=750 | mode=offline", duration, seed)
    client = OfflineStructuredOutputClient()
    previous = SAFE_FALLBACK.model_copy(deep=True)
    previous_panic = 0.0
    previous_entropy = visual_entropy_index(previous)
    accepted_count = fallback_count = 0
    records: list[dict[str, Any]] = []
    timeout_event = None

    for f in generate_feature_stream(duration, seed):
        telemetry = features_to_telemetry(f)
        stress = panic_index(telemetry)

        # compile_blueprint itself validates proposals. Passing previous ensures the
        # compiler's cross-window entropy gate is active.
        compiled = compile_blueprint(
            telemetry, client=client, timeout_seconds=DEADLINE_SECONDS,
            previous_blueprint=previous,
        )

        # Re-parse the structured result at the public untrusted-payload boundary.
        payload_result = validate_payload(
            compiled.model_dump_json(),
            expected_panic_index=stress,
            previous_panic_index=previous_panic,
            previous_visual_entropy_index=previous_entropy,
        )
        # A second explicit proposal pass makes the requested interface visible in
        # the orchestrator and retains the exact repository ValidationResult shape.
        result: ValidationResult = (
            validate_proposal(
                payload_result.blueprint,
                expected_panic_index=stress,
                previous_panic_index=previous_panic,
                previous_visual_entropy_index=previous_entropy,
            )
            if payload_result.accepted else payload_result
        )

        if result.accepted:
            previous = result.blueprint.model_copy(deep=True)
            previous_panic = stress
            previous_entropy = result.visual_entropy_index
            accepted_count += 1
        else:
            previous = SAFE_FALLBACK.model_copy(deep=True)
            fallback_count += 1

        codes = [v.code for v in result.violations]
        LOG.info(
            "t=%02ds | panic_index=%.4f | accepted=%s | flags=%s | violations=%s "
            "| entropy=%.4f | controls=%s",
            f.second, stress, result.accepted,
            "PASS" if result.accepted else "FAIL_CLOSED",
            codes or ["NONE"], result.visual_entropy_index, result.blueprint.visible_controls,
        )
        LOG.debug("ValidationResult=%s", result.model_dump_json())
        records.append({
            "second": f.second,
            "features": asdict(f),
            "panic_index": stress,
            "validation_result": result.model_dump(mode="json"),
        })

        if timeout_event is None and f.second == max(0, int(duration * 0.60)):
            timeout_event = timeout_ceiling_experiment()
        if realtime:
            time.sleep(1.0)

    graph_ok, graph_message = _nodes_reach_home(previous.navigation_graph)
    LOG.info("FINAL_GRAPH_AUDIT | valid=%s | home=%s | detail=%s",
             graph_ok, OPERATIONAL_HOME,
             graph_message or "all declared/referenced nodes can return home")
    if not graph_ok:
        previous = SAFE_FALLBACK.model_copy(deep=True)

    summary = {
        "duration_seconds": duration,
        "seed": seed,
        "samples": len(records),
        "accepted_count": accepted_count,
        "fallback_count": fallback_count,
        "final_panic_index": records[-1]["panic_index"] if records else 0.0,
        "final_visual_entropy_index": visual_entropy_index(previous),
        "final_graph_valid": graph_ok,
        "timeout_experiment": timeout_event,
        "records": records,
    }
    LOG.info("SIMULATION_COMPLETE | %s", json.dumps(
        {k: v for k, v in summary.items() if k != "records"}, sort_keys=True))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=60, help="synthetic one-second slices")
    parser.add_argument("--seed", type=int, default=7, help="deterministic random seed")
    parser.add_argument("--realtime", action="store_true", help="wait one second per sample")
    parser.add_argument("--verbose", action="store_true", help="print complete ValidationResult JSON")
    args = parser.parse_args()
    if args.duration < 1:
        parser.error("--duration must be >= 1")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s.%(msecs)03d | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        run_simulation(args.duration, args.seed, args.realtime)
    except KeyboardInterrupt:
        LOG.warning("SIMULATION_INTERRUPTED | no production system was connected")
        return 130
    except Exception:
        LOG.exception("SIMULATION_FAILED")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())