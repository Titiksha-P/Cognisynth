# Technical Architecture: Cognisynth

**Status:** Research architecture specification  
**Scope:** Telemetry-to-blueprint proposal, deterministic validation, and registered-element UI adaptation.

> **Safety boundary:** Cognisynth is a research prototype, not a certified safety system. This document distinguishes implemented behavior from target invariants that still require implementation and empirical validation.

## 1. System model and trust partition

Cognisynth treats adaptive interface synthesis as a constrained compilation problem, not direct model-controlled UI generation. The generative model proposes a declarative UIBlueprint; it does not produce executable JavaScript, HTML, selectors, or arbitrary DOM operations. The deterministic validator is the policy enforcement point, and the browser bridge is restricted to a registry of pre-approved element identifiers.

Three kinds of authority are separated:

1. **Observation authority:** telemetry ingestion normalizes measurements and attaches freshness and provenance metadata.
2. **Proposal authority:** the generative layer may propose a candidate layout, visibility set, navigation graph, and density score. Its output is untrusted.
3. **Execution authority:** the deterministic safety gate accepts or rejects the candidate. Only an accepted blueprint—or the deterministic fallback—may reach the runtime adapter.

A model response is data, never code. Schema validity is necessary but not sufficient: the candidate must also satisfy deterministic invariants.

## 2. The dual-engine epistemic loop

The **proposal engine** is probabilistic and may be incomplete, stale, inconsistent, or unavailable. The **verification engine** is deterministic with respect to its input snapshot and configured policy. The model can influence a candidate, but cannot waive a safety rule.

~~~mermaid
graph TD
    A[Raw operator actions and sensor events] --> B[Telemetry ingestion and timestamping]
    B --> C[Normalization, freshness, and range checks]
    C --> D[Feature vector and deterministic workload proxy]
    D --> E[Untrusted generative proposal layer]
    E --> F[Pydantic V2 schema parsing]
    F --> G[Deterministic safety gatekeeper]
    G --> H{All invariants satisfied?}
    H -->|Yes| I[Accepted declarative UIBlueprint]
    H -->|No, invalid, stale, or exception| J[Deterministic SAFE_FALLBACK]
    I --> K[Registry and DOM precondition checks]
    J --> K
    K --> L{Runtime preconditions hold?}
    L -->|Yes| M[Apply bounded DOM hot-patch]
    L -->|No| N[Abort mutation and reset to safe baseline]
    M --> O[Post-mutation audit and event log]
    N --> O
    O --> P[Next telemetry window]
    P --> B
~~~

### 2.1 Boundary contract

Let xₜ be the normalized telemetry snapshot at time t, g the untrusted proposal function, V the deterministic validator, b₀ the immutable fallback blueprint, and R the registered-element runtime adapter.

`cₜ = g(xₜ)`

`bₜ = V(cₜ, xₜ)` if schema and all invariants pass; otherwise `bₜ = b₀`.

The runtime may apply only `R(bₜ, registry)`. Model-authored executable content is never part of the runtime interface.

The boundary is fail-closed if every timeout, schema error, stale input, validation violation, registry mismatch, and unexpected exception prevents an unverified candidate from being applied. A runtime exception must abort the candidate mutation and trigger the host application's documented safe-baseline procedure; the prototype bridge alone does not establish atomic rollback.

### 2.2 Epistemic assumptions

- Telemetry is evidence about interaction state, not a direct measurement of internal cognitive state.
- The workload/panic index is a deterministic heuristic proxy, not a clinical or validated cognitive assessment.
- A valid Pydantic object establishes structural conformance only. It does not prove usability, safety, or empirical benefit.
- The fallback is a predefined conservative configuration whose suitability must be assessed for each target domain.
- Audit events should record timestamps, policy version, proposal hash, validation outcome, violation codes, and applied blueprint identity. Do not log secrets or identifiable telemetry without an approved protocol.

## 3. Telemetry ingestion and vector compiling

Represent a telemetry window as Xₜ = {eᵢ}, where each event contains an event type, monotonic timestamp, optional target identifier, and source-quality metadata. Features should be normalized against a calibrated baseline and accompanied by missingness and freshness indicators. The following are candidate research features; they are **not all implemented** in the current TelemetrySnapshot schema.

| Metric | Variable / symbol | Operational tracking function | Candidate computation and interpretation |
|---|---|---|---|
| Mouse Trajectory Fractal Dimension | mouse_fd, D_f | Tracks path irregularity, corrective movements, and changes in pointer-control strategy over a fixed window. | Estimate fractal dimension from sampled 2-D trajectory data using a declared estimator and fixed sampling policy. Compare with task/operator baseline; do not treat one value as diagnosis. |
| Keystroke Velocity Gradient | key_velocity_grad, G_k | Tracks acceleration or deceleration in inter-key timing and changes in input rhythm. | For inter-key intervals Δtᵢ, define vᵢ = 1/max(Δtᵢ, ε), then estimate G_k as a robust slope of vᵢ over time. Explicitly handle pauses, key repeats, and missing timestamps. |
| Alert Acknowledgment Latency | alert_ack_ms, L_a | Measures elapsed time between alert presentation and operator acknowledgment. | L_a = t_ack − t_present when timestamps are monotonic and refer to the same alert. Track unacknowledged alerts as censored events rather than dropping them. |
| Command Repetition Volatility | command_rep_vol, V_c | Tracks bursts or instability in repeated commands, retries, or near-identical actions within a window. | Compute robust dispersion over per-command repetition counts or inter-command intervals, such as normalized median absolute deviation. Distinguish legitimate repeated workflows from accidental repetition. |

### 3.1 Existing telemetry schema

The current core/cognitive_compiler.py defines TelemetrySnapshot with:

- **timestamp:** wall-clock timestamp used for staleness rejection.
- **heart_rate_bpm:** bounded to 25–240.
- **error_rate:** bounded to [0, 1].
- **response_latency_ms:** bounded to 0–120,000 ms.
- **alarm_rate:** bounded to 0–100.
- **workload_self_report:** bounded to 0–10.

The four interaction metrics above require schema, collection, privacy, calibration, and test additions before they can be described as implemented. Sensor provenance, clock synchronization, sampling frequency, window length, and missing-data behavior must be fixed in the experiment protocol.

### 3.2 Deterministic proxy

The current code computes a bounded heuristic p̂ₜ ∈ [0,1] from heart rate, error rate, response latency, alarm rate, and self-reported workload. It influences the density ceiling and is compared with the candidate's declared proxy. Its coefficients are design constants, not empirically estimated causal weights.

## 4. Trusted boundary guardrails

Let b be a candidate blueprint, H = (N,E) its directed navigation graph, Ccrit the set of critical controls, r(b) its declared render budget, d(b) its declared density score, pₜ the deterministic telemetry proxy, and b₀ the fallback.

### 4.1 Temporal ceiling compliance

The target frame budget is T_frame = 1000/60 ms ≈ 16.67 ms. A candidate passes the declared-budget check only if:

`0 < r(b) ≤ 16.67 ms`

The current validator enforces this inequality on render_budget_ms. This is a declared-budget check, not proof that end-to-end browser rendering completes within 16.67 ms. That claim requires instrumented measurements of validation, DOM mutation, layout, paint, and scheduling under representative loads.

### 4.2 Control graph reachability

For operational home node h = home, every critical control must be reachable by a directed path:

`∀c ∈ Ccrit, ∃ path h ↝ c in H`

The current validator traverses the graph from home **when that node exists**. A stronger contract should reject a missing home node explicitly; otherwise reachability cannot be established. Visibility and reachability are separate obligations.

### 4.3 Cognitive information-density monotonicity

The current implementation enforces a panic-dependent density ceiling:

`d(b) ≤ d_max(pₜ) = 3.0 − 1.5 × clip(pₜ, 0, 1)`

It also computes a heuristic visual entropy proxy:

`E_v(b) = 0.35 log₂(1+n_v) + 0.10 log₂(1+n_e) + 0.05 log₂(1+n_c)`

Here n_v is the count of visible controls and streams, n_e the navigation-edge count, and n_c the count of collapsed streams. This is not a validated cognitive-load metric.

The validator supports a cross-window monotonicity invariant when the caller supplies the previous accepted blueprint state. For successive accepted blueprints within a defined escalation regime, require:

`pₜ₊₁ ≥ pₜ ⇒ E_v(bₜ₊₁) ≤ E_v(bₜ) + δ`

where δ ≥ 0 is an experimentally justified tolerance. The regime, hysteresis, recovery behavior, and measurement window must be specified to avoid oscillatory UI changes. The compiler accepts previous_blueprint and supplies its panic index and visual entropy to the validator. The first window has no prior state to compare. Callers must pass the last accepted blueprint on subsequent windows; otherwise only the density-score ceiling is enforced. The tolerance defaults to zero, so entropy must be non-increasing as stress rises.

### 4.4 Fail-closed fallback routing

Let S(b,x) be the conjunction of schema validity, freshness, visibility, reachability, timing, density, non-blocking behavior, friction constraints, and telemetry-consistency checks. The target routing rule is:

`b_out = b` when S(b,x) is true; otherwise `b_out = b₀`.

The current compiler returns a deep copy of SAFE_FALLBACK for stale telemetry, client initialization failure, empty parsed responses, rejected validation, and caught exceptions. The runtime bridge rejects missing critical controls and unregistered or missing DOM elements. Production-grade recovery still needs an integration test proving that an aborted mutation cannot leave a partially updated interface.

### 4.5 Pydantic V2 contract role

UIBlueprint, Violation, and ValidationResult use Pydantic V2-style models and field constraints. extra="forbid" rejects undeclared blueprint fields. Type/range validation is the first layer; graph, timing, density, and critical-control checks remain explicit deterministic code. Pydantic validation alone is not a formal proof of system invariants.

## 5. Implementation status and verification obligations

| Property | Current repository behavior | Evidence still required |
|---|---|---|
| Frame-budget declaration | Enforces render_budget_ms ≤ 16.67 | Browser timing measurements and worst-case analysis |
| Critical controls | Checks all three identifiers are visible | Domain-specific hazard analysis and end-to-end UI tests |
| Graph reachability | Traverses from home if present | Reject absent home explicitly; test graph edge cases |
| Density ceiling | Enforces panic-dependent density_score ceiling | Calibrate threshold against human-subject data |
| Visual entropy | Computes heuristic proxy and rejects an increase when prior state is supplied and stress rises | Calibrate proxy; ensure callers consistently pass the last accepted blueprint |
| Fail-closed proposal handling | Compiler returns fallback on common error paths | Fault injection, exception-path tests, runtime rollback tests |
| Inference deadline | Compiler default currently uses a 4.0 s timeout | Set and test the intended 750 ms policy in code |
| DOM mutation | Uses registered IDs and rejects missing elements | Atomic patching or documented rollback semantics |

## 6. Local verification and Git workflow

From a local clone, run the existing offline checks:

~~~bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m safety.formal_validator
node --check core/jspsych_runtime_bridge.js
~~~

To create or edit this document locally and push it to the repository:

~~~bash
git checkout main
git pull --ff-only origin main
mkdir -p docs
# Save the complete contents of this document as docs/TECHNICAL_ARCHITECTURE.md
${EDITOR:-vi} docs/TECHNICAL_ARCHITECTURE.md
git add docs/TECHNICAL_ARCHITECTURE.md
git commit -m "docs: add technical architecture specification"
git push origin main
~~~

Repository remote: https://github.com/Titiksha-P/Cognisynth.git. If branch protection is enabled, push a feature branch and open a pull request instead.

## 7. Research and safety disclaimer

Cognisynth is an experimental software scaffold. Synthetic telemetry, heuristic workload proxies, deterministic validators, and passing unit tests do not establish human-factors validity or operational safety. Before any high-stakes deployment, require independent threat modeling, hazard analysis, verified recovery behavior, accessibility testing, privacy review, empirical calibration, human-subject evaluation, and domain-specific certification.
