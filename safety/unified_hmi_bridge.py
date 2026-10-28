"""Unified, fail-closed HMI safety bridge for Cognisynth.

This module is a research integration boundary, not a certified medical or safety
instrument. Physiological and linguistic features are deterministic proxies only;
they are not clinically validated measurements of panic, workload, or intent.
Untrusted text is treated strictly as data and is never evaluated as code.
"""
from __future__ import annotations

import json
import math
import re
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from safety.formal_validator import (
    SAFE_FALLBACK,
    UIBlueprint,
    ValidationResult,
    _nodes_reach_home,
    validate_payload,
)

TOKEN_WINDOW_SIZE = 15
PANIC_TERMS = frozenset(
    {
        "panic", "panicking", "urgent", "urgently", "emergency", "danger",
        "dangerous", "help", "failure", "failed", "failing", "critical",
        "trapped", "stuck", "cannot", "can't", "unable", "immediately",
        "now", "alarm", "alarms", "error", "errors", "overwhelmed",
    }
)
URGENCY_MARKERS = frozenset({"!", "!!", "!!!", "??", "??!"})
_TOKEN_PATTERN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?|[!?]{1,3}|\\.{2,}")


class NeuralHMIPayload(BaseModel):
    """Validated physical telemetry; values are inputs, not diagnoses."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
    )

    response_latency_ms: float = Field(ge=0, le=120_000)
    eeg_theta_power: float = Field(ge=0, le=1_000_000)
    eeg_alpha_power: float = Field(ge=0, le=1_000_000)
    galvanic_skin_response: float = Field(ge=0, le=1_000_000)


class LinguisticPanicMetric(BaseModel):
    """One deterministic metric for a rolling 15-token text window."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    token_count: int = Field(ge=TOKEN_WINDOW_SIZE)
    window_start_token: int = Field(ge=0)
    window_end_token: int = Field(ge=TOKEN_WINDOW_SIZE)
    panic_score: float = Field(ge=0, le=1)
    panic_term_count: int = Field(ge=0)
    urgency_marker_count: int = Field(ge=0)


class BridgeDecision(BaseModel):
    """Auditable result returned by the unified entry proxy."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    blueprint: UIBlueprint
    panic_index: float = Field(ge=0, le=1)
    graph_verified: bool
    violations: list[str] = Field(default_factory=list)


class TelemetryStreamError(ValueError):
    """Raised when an input stream violates the declared text-stream contract."""


def neural_panic_proxy(payload: NeuralHMIPayload) -> float:
    """Map physical channels to a bounded heuristic proxy in [0, 1].

    This deliberately transparent normalization is suitable for exercising the
    safety pipeline, not for physiological inference or clinical interpretation.
    Sensor calibration, subject baselines, motion artifacts, and sampling rates
    are outside this prototype's validated scope.
    """
    latency_component = min(payload.response_latency_ms / 5_000.0, 1.0)

    theta_total = payload.eeg_theta_power + payload.eeg_alpha_power
    theta_component = (
        payload.eeg_theta_power / theta_total if theta_total > 0.0 else 0.0
    )

    # A saturating transform prevents large raw GSR values from dominating.
    gsr_component = payload.galvanic_skin_response / (
        payload.galvanic_skin_response + 5.0
    )

    score = (
        0.40 * latency_component
        + 0.30 * theta_component
        + 0.30 * gsr_component
    )
    return round(min(1.0, max(0.0, score)), 4)


def _metric_for_window(tokens: list[str], start_index: int) -> LinguisticPanicMetric:
    normalized = [token.casefold().replace("’", "'") for token in tokens]
    panic_count = sum(token in PANIC_TERMS for token in normalized)
    urgency_count = sum(token in URGENCY_MARKERS for token in tokens)

    # This is a transparent lexical indicator, not a semantic or psychometric
    # model. Unknown wording, negation, sarcasm, and language variation can mislead it.
    term_component = min(panic_count / 4.0, 1.0)
    urgency_component = min(urgency_count / 3.0, 1.0)
    score = 0.8 * term_component + 0.2 * urgency_component

    return LinguisticPanicMetric(
        token_count=len(tokens),
        window_start_token=start_index,
        window_end_token=start_index + len(tokens),
        panic_score=round(score, 4),
        panic_term_count=panic_count,
        urgency_marker_count=urgency_count,
    )


async def parse_telemetry_stream(
    raw_text_generator: AsyncIterable[str],
) -> AsyncIterator[LinguisticPanicMetric]:
    """Yield a metric for each rolling 15-token window from an async text stream.

    Each incoming text fragment is tokenized without executing or interpreting
    its contents. Once 15 tokens have arrived, a new metric is emitted for each
    subsequent token (stride 1), retaining only the latest 15 tokens. This keeps
    memory bounded and supports incremental streams. No raw text is executed,
    interpolated into a command, or parsed as an expression.
    """
    window: deque[str] = deque(maxlen=TOKEN_WINDOW_SIZE)
    token_index = 0

    try:
        async for fragment in raw_text_generator:
            if not isinstance(fragment, str):
                raise TelemetryStreamError(
                    "Telemetry stream fragments must be strings."
                )

            for token in _TOKEN_PATTERN.findall(fragment):
                window.append(token)
                token_index += 1
                if len(window) == TOKEN_WINDOW_SIZE:
                    start_index = token_index - TOKEN_WINDOW_SIZE
                    yield _metric_for_window(list(window), start_index)
    except TelemetryStreamError:
        raise
    except Exception as exc:
        # Propagate a typed error so callers can fail closed at their boundary.
        raise TelemetryStreamError("Unable to consume telemetry text stream.") from exc


class CognisynthSafetyBridge:
    """Unified input proxy that never bypasses deterministic HMI validation."""

    def __init__(self) -> None:
        self._fallback = SAFE_FALLBACK.model_copy(deep=True)

    @staticmethod
    def parse_neural_payload(
        payload: NeuralHMIPayload | Mapping[str, Any] | str | bytes,
    ) -> NeuralHMIPayload:
        """Validate structured physical inputs; reject extras and non-finite values."""
        if isinstance(payload, NeuralHMIPayload):
            return payload
        if isinstance(payload, (str, bytes, bytearray)):
            # JSON is a data format. Unlike eval(), JSON decoding cannot execute code.
            parsed = json.loads(payload)
            if not isinstance(parsed, dict):
                raise ValueError("Neural telemetry JSON must contain an object.")
            return NeuralHMIPayload.model_validate(parsed)
        if isinstance(payload, Mapping):
            return NeuralHMIPayload.model_validate(dict(payload))
        raise TypeError("Unsupported neural telemetry payload type.")

    def validate_candidate(
        self,
        neural_payload: NeuralHMIPayload | Mapping[str, Any] | str | bytes,
        candidate: UIBlueprint | Mapping[str, Any] | str | bytes,
        *,
        linguistic_panic_score: float | None = None,
        previous_panic_index: float | None = None,
        previous_visual_entropy_index: float | None = None,
    ) -> BridgeDecision:
        """Validate a proposed blueprint and fail closed on any malformed input.

        The graph reachability check is run explicitly at this boundary, then the
        existing validator performs the rest of the deterministic invariants.
        An untrusted model rationale is never interpreted as a policy or command.
        """
        try:
            neural = self.parse_neural_payload(neural_payload)
            physical_score = neural_panic_proxy(neural)

            if linguistic_panic_score is not None:
                if (
                    not math.isfinite(linguistic_panic_score)
                    or not 0.0 <= linguistic_panic_score <= 1.0
                ):
                    raise ValueError("linguistic_panic_score must be finite and in [0, 1].")
                # Use a bounded, deterministic blend; linguistic features are only
                # a secondary proxy and never override the physical safety checks.
                expected_panic = round(0.75 * physical_score + 0.25 * linguistic_panic_score, 4)
            else:
                expected_panic = physical_score

            if isinstance(candidate, UIBlueprint):
                proposal = candidate
            elif isinstance(candidate, (str, bytes, bytearray)):
                decoded = json.loads(candidate)
                if not isinstance(decoded, dict):
                    raise ValueError("Candidate JSON must contain an object.")
                proposal = UIBlueprint.model_validate(decoded)
            elif isinstance(candidate, Mapping):
                proposal = UIBlueprint.model_validate(dict(candidate))
            else:
                raise TypeError("Unsupported candidate payload type.")

            graph_valid, graph_message = _nodes_reach_home(proposal.navigation_graph)
            if not graph_valid:
                return self._reject(expected_panic, graph_message)

            validation: ValidationResult = validate_payload(
                proposal.model_dump(mode="json"),
                expected_panic_index=expected_panic,
                previous_panic_index=previous_panic_index,
                previous_visual_entropy_index=previous_visual_entropy_index,
            )
            if not validation.accepted:
                return BridgeDecision(
                    accepted=False,
                    blueprint=self._fallback.model_copy(deep=True),
                    panic_index=expected_panic,
                    graph_verified=graph_valid,
                    violations=[item.code + ": " + item.message for item in validation.violations],
                )

            return BridgeDecision(
                accepted=True,
                blueprint=validation.blueprint,
                panic_index=expected_panic,
                graph_verified=True,
                violations=[],
            )
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._reject(0.0, f"INPUT_REJECTED: {type(exc).__name__}: {exc}")
        except Exception as exc:
            # Unexpected errors also fail closed; never return a candidate after an
            # exception in parsing, graph verification, or validation.
            return self._reject(0.0, f"INTERNAL_FAIL_CLOSED: {type(exc).__name__}")

    def _reject(self, panic_index: float, reason: str) -> BridgeDecision:
        return BridgeDecision(
            accepted=False,
            blueprint=self._fallback.model_copy(deep=True),
            panic_index=min(1.0, max(0.0, panic_index)),
            graph_verified=False,
            violations=[reason],
        )

    async def consume_linguistic_stream(
        self, raw_text_generator: AsyncIterable[str]
    ) -> list[LinguisticPanicMetric]:
        """Consume the stream into metrics; callers may instead iterate directly."""
        metrics: list[LinguisticPanicMetric] = []
        async for metric in parse_telemetry_stream(raw_text_generator):
            metrics.append(metric)
        return metrics
