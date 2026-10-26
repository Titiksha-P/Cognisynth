"""Telemetry-to-blueprint compiler. Model output is always passed through a deterministic validator."""
from __future__ import annotations

import math
import os
import time
from typing import Optional

from pydantic import BaseModel, Field

from safety.formal_validator import (
    UIBlueprint,
    SAFE_FALLBACK,
    validate_proposal,
    visual_entropy_index,
)


class TelemetrySnapshot(BaseModel):
    timestamp: float = Field(default_factory=time.time)
    heart_rate_bpm: float = Field(ge=25, le=240)
    error_rate: float = Field(ge=0, le=1)
    response_latency_ms: float = Field(ge=0, le=120000)
    alarm_rate: float = Field(ge=0, le=100)
    workload_self_report: float = Field(default=0.0, ge=0, le=10)


def panic_index(t: TelemetrySnapshot) -> float:
    """Deterministic, interpretable synthetic panic proxy in [0, 1]."""
    hr = min(1.0, max(0.0, (t.heart_rate_bpm - 60.0) / 100.0))
    latency = min(1.0, t.response_latency_ms / 5000.0)
    alarms = min(1.0, t.alarm_rate / 10.0)
    workload = t.workload_self_report / 10.0
    return round(min(1.0, max(0.0, 0.30 * hr + 0.25 * t.error_rate
                            + 0.20 * latency + 0.15 * alarms + 0.10 * workload)), 4)


def compile_blueprint(
    telemetry: TelemetrySnapshot,
    *,
    client=None,
    timeout_seconds: float = 0.75,
    max_age_seconds: float = 10.0,
    previous_blueprint: Optional[UIBlueprint] = None,
) -> UIBlueprint:
    """Return a validated blueprint or deterministic fallback; never trust raw model output.

    The inference/parse path is capped at 750 ms. SDK retries are disabled so a retry
    cannot silently extend the deadline. Any late response fails closed to SAFE_FALLBACK.
    """
    if time.time() - telemetry.timestamp > max_age_seconds:
        return SAFE_FALLBACK.model_copy(deep=True)

    # Enforce the architectural ceiling even if a caller supplies a larger timeout.
    request_timeout = min(max(float(timeout_seconds), 0.001), 0.75)
    if client is None:
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=os.environ.get("OPENAI_API_KEY"),
                timeout=request_timeout,
                max_retries=0,
            )
        except Exception:
            return SAFE_FALLBACK.model_copy(deep=True)
    prompt = (
        "Propose a conservative UI blueprint as structured data. Never hide critical controls. "
        "Use only registered identifiers. Treat telemetry as noisy. "
        f"Telemetry: panic_index={panic_index(telemetry)}, error_rate={telemetry.error_rate}, "
        f"response_latency_ms={telemetry.response_latency_ms}, alarm_rate={telemetry.alarm_rate}. "
        "Keep the interface simple and avoid interaction friction."
    )
    try:
        deadline = time.monotonic() + request_timeout
        response = client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You propose UI blueprints; deterministic code decides safety."},
                {"role": "user", "content": prompt},
            ],
            response_format=UIBlueprint,
            timeout=request_timeout,
        )
        # SDK/network timeouts bound the blocking request; this monotonic check also
        # rejects late parse results and prevents their application to a later window.
        if time.monotonic() > deadline:
            return SAFE_FALLBACK.model_copy(deep=True)
        proposal = response.choices[0].message.parsed
        if proposal is None:
            return SAFE_FALLBACK.model_copy(deep=True)
        previous_state = {}
        if previous_blueprint is not None:
            previous_state = {
                "previous_panic_index": previous_blueprint.panic_index,
                "previous_visual_entropy_index": visual_entropy_index(previous_blueprint),
            }
        result = validate_proposal(
            proposal,
            expected_panic_index=panic_index(telemetry),
            **previous_state,
        )
        return result.blueprint if result.accepted else SAFE_FALLBACK.model_copy(deep=True)
    except Exception:
        return SAFE_FALLBACK.model_copy(deep=True)
