"""Deterministic safety gate for candidate cognitive UI blueprints.

This is a research prototype, not a certified safety system.
"""
from __future__ import annotations

import json
import math
from typing import List, Dict, Any

from pydantic import BaseModel, Field, ConfigDict, ValidationError, model_validator

FRAME_BUDGET_MS = 16.67
OPERATIONAL_HOME = "home"
CRITICAL_CONTROLS = {"master_emergency_shutoff", "alarm_acknowledge", "manual_override"}


def _graph_nodes(navigation_graph: Dict[str, List[str]]) -> set[str]:
    nodes = set(navigation_graph)
    for targets in navigation_graph.values():
        nodes.update(targets)
    return nodes


def _nodes_reach_home(navigation_graph: Dict[str, List[str]]) -> tuple[bool, str]:
    """Require every declared/referenced navigation node to have a path to operational home."""
    nodes = _graph_nodes(navigation_graph)
    if OPERATIONAL_HOME not in nodes:
        return False, f"Navigation graph must include OPERATIONAL_HOME={OPERATIONAL_HOME!r}."

    # Reverse edges so a single traversal identifies every node with a path to home.
    reverse: dict[str, list[str]] = {node: [] for node in nodes}
    for source, targets in navigation_graph.items():
        for target in targets:
            reverse.setdefault(target, []).append(source)

    can_reach_home = {OPERATIONAL_HOME}
    stack = [OPERATIONAL_HOME]
    while stack:
        node = stack.pop()
        for predecessor in reverse.get(node, []):
            if predecessor not in can_reach_home:
                can_reach_home.add(predecessor)
                stack.append(predecessor)

    unreachable = sorted(nodes - can_reach_home)
    if unreachable:
        return False, (
            "Every navigation node must have a directed path back to "
            f"OPERATIONAL_HOME={OPERATIONAL_HOME!r}; unreachable nodes: {unreachable}."
        )
    return True, ""


class UIBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visible_controls: List[str] = Field(min_length=1)
    visible_streams: List[str] = Field(default_factory=list)
    collapsed_streams: List[str] = Field(default_factory=list)
    navigation_graph: Dict[str, List[str]] = Field(default_factory=dict)
    interaction_friction_ms: Dict[str, int] = Field(default_factory=dict)
    async_blocking: bool = False
    render_budget_ms: float = Field(gt=0)
    panic_index: float = Field(ge=0, le=1)
    density_score: float = Field(ge=0)
    rationale: str = ""

    @model_validator(mode="after")
    def every_node_can_return_home(self) -> "UIBlueprint":
        valid, message = _nodes_reach_home(self.navigation_graph)
        if not valid:
            raise ValueError(message)
        return self


class Violation(BaseModel):
    code: str
    message: str


class ValidationResult(BaseModel):
    accepted: bool
    blueprint: UIBlueprint
    violations: List[Violation] = Field(default_factory=list)
    visual_entropy_index: float = 0.0


SAFE_FALLBACK = UIBlueprint(
    visible_controls=[
        "master_emergency_shutoff",
        "alarm_acknowledge",
        "manual_override",
    ],
    visible_streams=["primary_status"],
    collapsed_streams=[],
    navigation_graph={
        "home": ["primary_status", "master_emergency_shutoff", "alarm_acknowledge", "manual_override"],
        "primary_status": ["home", "alarm_acknowledge", "manual_override"],
        "master_emergency_shutoff": ["home"],
        "alarm_acknowledge": ["home"],
        "manual_override": ["home"],
    },
    interaction_friction_ms={
        "master_emergency_shutoff": 0,
        "alarm_acknowledge": 0,
        "manual_override": 0,
    },
    async_blocking=False,
    render_budget_ms=8.0,
    panic_index=0.0,
    density_score=1.0,
    rationale="Deterministic conservative fallback.",
)


def visual_entropy_index(blueprint: UIBlueprint) -> float:
    """Calibrated heuristic proxy; not a validated cognitive-load measurement."""
    visible_elements = len(blueprint.visible_controls) + len(blueprint.visible_streams)
    edge_count = sum(len(targets) for targets in blueprint.navigation_graph.values())
    collapsed_count = len(blueprint.collapsed_streams)
    return (
        0.35 * math.log2(1 + visible_elements)
        + 0.10 * math.log2(1 + edge_count)
        + 0.05 * math.log2(1 + collapsed_count)
    )


def validate_proposal(proposal: UIBlueprint, expected_panic_index: float) -> ValidationResult:
    violations: list[Violation] = []

    def add(code: str, message: str) -> None:
        violations.append(Violation(code=code, message=message))

    visible = set(proposal.visible_controls)
    for control in sorted(CRITICAL_CONTROLS):
        if control not in visible:
            add("CRITICAL_CONTROL_HIDDEN", f"Critical control must remain visible: {control}")

    if proposal.render_budget_ms > FRAME_BUDGET_MS:
        add("FRAME_BUDGET_EXCEEDED", f"Render budget {proposal.render_budget_ms}ms exceeds {FRAME_BUDGET_MS}ms.")

    for control, delay in proposal.interaction_friction_ms.items():
        if control in CRITICAL_CONTROLS and delay != 0:
            add("CRITICAL_CONTROL_FRICTION", f"Critical control {control} must have zero interaction friction.")
        elif delay != 0 and not 200 <= delay <= 600:
            add("FRICTION_OUT_OF_RANGE", f"Friction for {control} must be zero or between 200 and 600ms.")

    if proposal.async_blocking:
        add("ASYNC_BLOCKING_PROHIBITED", "Blocking asynchronous work is prohibited in the UI runtime.")

    graph_valid, graph_message = _nodes_reach_home(proposal.navigation_graph)
    if not graph_valid:
        add("DANGLING_GRAPH_EDGE", graph_message)

    # Retain the explicit home-to-critical-control reachability requirement as a separate invariant.
    graph_nodes = _graph_nodes(proposal.navigation_graph)
    if OPERATIONAL_HOME in graph_nodes:
        reachable = set()
        stack = [OPERATIONAL_HOME]
        while stack:
            node = stack.pop()
            if node in reachable:
                continue
            reachable.add(node)
            stack.extend(proposal.navigation_graph.get(node, []))
        for control in CRITICAL_CONTROLS:
            if control not in reachable:
                add("CRITICAL_CONTROL_UNREACHABLE", f"Critical control is not reachable from OPERATIONAL_HOME: {control}")

    expected_density_ceiling = 3.0 - 1.5 * max(0.0, min(1.0, expected_panic_index))
    if proposal.density_score > expected_density_ceiling:
        add("DENSITY_CEILING_EXCEEDED", f"Density {proposal.density_score} exceeds ceiling {expected_density_ceiling:.3f}.")

    if abs(proposal.panic_index - expected_panic_index) > 0.20:
        add("PANIC_INDEX_MISMATCH", "Blueprint panic index differs materially from deterministic telemetry estimate.")

    if violations:
        return ValidationResult(
            accepted=False,
            blueprint=SAFE_FALLBACK.model_copy(deep=True),
            violations=violations,
            visual_entropy_index=visual_entropy_index(SAFE_FALLBACK),
        )
    return ValidationResult(
        accepted=True,
        blueprint=proposal,
        violations=[],
        visual_entropy_index=visual_entropy_index(proposal),
    )


def validate_payload(payload: str | bytes | Dict[str, Any], expected_panic_index: float = 0.0) -> ValidationResult:
    """Parse untrusted JSON/object payloads and fail closed on Pydantic or invariant errors."""
    try:
        if isinstance(payload, (str, bytes, bytearray)):
            proposal = UIBlueprint.model_validate_json(payload)
        else:
            proposal = UIBlueprint.model_validate(payload)
    except ValidationError as exc:
        return ValidationResult(
            accepted=False,
            blueprint=SAFE_FALLBACK.model_copy(deep=True),
            violations=[Violation(code="SCHEMA_VALIDATION_ERROR", message=str(exc))],
            visual_entropy_index=visual_entropy_index(SAFE_FALLBACK),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return ValidationResult(
            accepted=False,
            blueprint=SAFE_FALLBACK.model_copy(deep=True),
            violations=[Violation(code="SCHEMA_VALIDATION_ERROR", message=str(exc))],
            visual_entropy_index=visual_entropy_index(SAFE_FALLBACK),
        )
    return validate_proposal(proposal, expected_panic_index=expected_panic_index)


def main() -> None:
    result = validate_proposal(SAFE_FALLBACK, expected_panic_index=0.0)
    if not result.accepted:
        raise SystemExit("OFFLINE VERIFICATION: FAIL")
    print("OFFLINE VERIFICATION: PASS")


if __name__ == "__main__":
    main()
