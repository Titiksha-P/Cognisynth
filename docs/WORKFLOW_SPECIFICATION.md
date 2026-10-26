# Workflow Specification: Cognisynth

**Status:** Execution and degradation specification for the research prototype  
**Primary modules:** core/cognitive_compiler.py, safety/formal_validator.py, core/jspsych_runtime_bridge.js

> **Safety boundary:** This document specifies intended orchestration behavior and distinguishes it from behavior currently implemented in the repository.

## 1. Purpose and execution model

The workflow converts a timestamped telemetry snapshot into a candidate declarative UI blueprint, subjects it to deterministic validation, and passes only an accepted blueprint or a fixed fallback to the runtime adapter. The language model is a proposal service, not a policy authority.

The target workflow uses a **750 ms inference deadline**. The deadline is measured from dispatch of the model request to receipt of a usable structured response. If no usable response arrives by the deadline, the candidate is abandoned and fallback is selected. Retries must not reset the deadline.

**Implementation alignment note:** current compile_blueprint defaults to timeout_seconds=4.0. The 750 ms gate below is a target requirement, not a claim that the current code already enforces it. To implement it, set the timeout to at most 0.750 seconds and add a monotonic deadline check around the complete request/parse path, then test the timeout branch.

## 2. Real-time sequence execution blueprint

~~~mermaid
sequenceDiagram
    autonumber
    actor Human as Human Operator
    participant Ingest as Telemetry Ingestion Engine
    participant Compiler as cognitive_compiler.py
    participant API as OpenAI Structured Interface
    participant Validator as formal_validator.py
    participant Bridge as jspsych_runtime_bridge.js

    Human->>Ingest: Mouse, keyboard, alert, command events
    Ingest->>Ingest: Normalize, timestamp, range-check, freshness-check
    Ingest->>Compiler: TelemetrySnapshot plus provenance
    Compiler->>Compiler: Compute deterministic proxy and snapshot age
    alt Snapshot stale or invalid
        Compiler->>Validator: Select SAFE_FALLBACK
        Validator-->>Compiler: Fallback blueprint
    else Snapshot eligible
        Compiler->>Compiler: Start monotonic 750 ms deadline
        Compiler->>API: Structured blueprint request
        par Inference request and deadline gate
            API-->>Compiler: Parsed UIBlueprint candidate
        and Active 750 ms timeout gate
            Compiler->>Compiler: Enforce deadline <= 750 ms
        end
        alt Response arrives within deadline and parses
            Compiler->>Validator: Candidate plus expected proxy
            Validator->>Validator: Schema and deterministic invariants
            alt All implemented invariants pass
                Validator-->>Compiler: Accepted blueprint plus audit result
            else Any invariant fails
                Validator-->>Compiler: Rejection, violation codes, fallback
            end
        else Timeout, API error, or parse failure
            Compiler->>Compiler: Abandon candidate; never apply late result
            Compiler->>Validator: Select SAFE_FALLBACK
            Validator-->>Compiler: Fallback blueprint
        end
    end
    Compiler->>Bridge: Approved blueprint or fallback only
    Bridge->>Bridge: Check critical controls and registry preconditions
    alt Runtime preconditions pass
        Bridge->>Bridge: Apply bounded patch to registered elements
        Bridge-->>Human: Updated UI; retain critical controls
    else Runtime precondition fails
        Bridge->>Bridge: Abort patch; invoke host safe-baseline recovery
        Bridge-->>Human: Safe baseline / recovery state
    end
    Bridge-->>Ingest: Audit event and next-window marker
~~~

### 2.1 Deadline semantics

Let t₀ be monotonic time immediately before inference dispatch and D = 750 ms. A candidate is timely only when:

`t_response − t₀ ≤ D`

and its response has been parsed and is ready for validation before the deadline. This is an inference/parse gate, not a guarantee that validation and DOM mutation finish inside the same 750 ms. The independent frame-budget invariant targets a declared render budget of 16.67 ms.

Required behavior:

1. Start one monotonic deadline before dispatch; do not use wall-clock time for elapsed-duration measurement.
2. Bound the request timeout to the remaining deadline.
3. Treat timeout, cancellation, API failure, malformed structured output, and late responses as rejected candidates.
4. Never apply a late response to a later telemetry window.
5. Route to SAFE_FALLBACK without waiting for a retry.
6. Record deadline outcome and incident code without recording secrets or unnecessary personal telemetry.

The current compiler catches exceptions and returns fallback, but its default API timeout is 4.0 seconds and it does not expose a dedicated TIMEOUT_CEILING_BREACH record. Implement and test these details before claiming conformance.

## 3. Finite-state workflow

| State | Entry condition | Permitted operation | Exit condition |
|---|---|---|---|
| INGEST | New operator/sensor event window | Normalize, timestamp, validate ranges | Valid fresh snapshot, or fallback |
| COMPILE | Eligible snapshot | Compute deterministic proxy; prepare structured request | Request dispatched with one 750 ms deadline |
| INFER | Request in flight | Await structured response without blocking UI thread | Parsed response before deadline, or timeout/error |
| VALIDATE | Candidate parsed before deadline | Run Pydantic schema and deterministic checks | Accepted candidate or rejection |
| FALLBACK | Stale input, timeout, error, or rejected candidate | Select fixed fallback blueprint | Fallback ready for runtime checks |
| PREPARE_PATCH | Accepted blueprint or fallback | Resolve IDs through trusted registry; verify preconditions | Patch permitted, or abort |
| APPLY | Runtime preconditions pass | Mutate only registered DOM properties | Post-patch audit |
| RECOVER | Patch precondition/runtime failure | Abort further mutation; restore host-defined safe baseline | Recovery audit and next window |
| AUDIT | Terminal outcome | Record outcome, policy version, timings, violations | Return to INGEST |

Transitions must be explicit. No transition from INFER directly to APPLY is allowed. No unvalidated candidate may be cached as an approved blueprint.

## 4. System incident and degradation matrix

| Incident code | Trigger event | Immediate architectural action | Target resolution |
|---|---|---|---|
| TIMEOUT_CEILING_BREACH | Structured inference/parse does not complete within 750 ms, or response arrives after deadline | Cancel or abandon candidate; discard late output; route to SAFE_FALLBACK; log elapsed time and deadline status | Keep UI responsive and apply only fallback. Current code needs explicit 750 ms enforcement and incident telemetry. |
| CRITICAL_CONTROL_HIDDEN | Candidate omits master_emergency_shutoff, alarm_acknowledge, or manual_override from visible_controls | Reject entire candidate; return fallback; do not partially apply visibility changes | Preserve all critical controls and add regression tests for each missing control. Validator and bridge currently check visibility. |
| DANGLING_GRAPH_EDGE | Edge targets an undeclared/invalid node, or critical control is unreachable from operational home | Reject candidate and use fallback; emit graph-specific diagnostics | Validate node/edge membership and reachability from an explicitly required home node. Current code checks reachability only if home exists; explicit dangling-edge detection remains a hardening item. |
| DENSITY_CEILING_VIOLATION | density_score exceeds d_max = 3.0 − 1.5 × clip(pₜ, 0, 1) | Reject candidate; route to fallback; record score and ceiling | Require a lower-density proposal or keep fallback. Current code enforces density-score ceiling; cross-window visual-entropy monotonicity is not implemented. |
| FRAME_BUDGET_EXCEEDED | Declared render_budget_ms > 16.67 | Reject candidate and select fallback | Keep declared budget within one 60 Hz frame; measure actual browser timings separately. |
| STALE_TELEMETRY | Snapshot age exceeds configured maximum or freshness cannot be established | Do not adapt from stale data; return fallback | Resume proposals only with a fresh valid snapshot. Current compiler rejects snapshots older than configured maximum age. |
| SCHEMA_OR_PARSE_FAILURE | Structured response is absent, malformed, or violates Pydantic model | Discard response and select fallback | Fix model/schema integration offline; raw response must never bypass validation. |
| REGISTRY_MISMATCH | Blueprint references an unregistered ID or registered DOM element is missing | Abort mutation and initiate host-defined safe-baseline recovery | Repair registry and test missing-element paths. Bridge rejects unregistered IDs and missing elements. |
| ASYNC_BLOCKING_PROHIBITED | Candidate requests blocking asynchronous work on UI runtime | Reject candidate and select fallback | Keep network/model work off UI thread; validator rejects async_blocking=True. |
| PANIC_INDEX_MISMATCH | Candidate proxy differs from deterministic expected proxy by more than configured tolerance | Reject candidate and select fallback | Recompute from authoritative snapshot; do not trust model-authored state estimates. |

The table describes target incident semantics. Some rows map to existing validator behavior, while dedicated codes, graph-edge checks, timing instrumentation, and recovery tests remain implementation work. A code name in this document is not evidence that the code is already emitted by the runtime.

## 5. Environment dependency specifications

### Required runtimes

- **Python:** 3.10 or newer; CI currently runs Python 3.11.
- **Node.js:** Node 20 in CI; the required check is the built-in JavaScript parser via node --check. No npm package is required for that syntax check.
- **Git:** required for local clone, commit, and push.
- **API credentials:** OPENAI_API_KEY is required only for live compiler calls. Offline validator and unit tests should run without it. Never commit credentials or a populated .env file.

### Mandatory Python package bounds

The repository requirements.txt currently declares:

- **pydantic>=2.7,<3.0** — Pydantic V2 schema/model validation.
- **openai>=1.30,<3.0** — structured model interface.

Use a virtual environment and install the repository's declared policy range rather than unconstrained package versions.

### Reproducible local checks

~~~bash
python --version
node --version
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m safety.formal_validator
python -m tests.telemetry_simulator --samples 30 --rate 10 --seed 7
node --check core/jspsych_runtime_bridge.js
~~~

The synthetic telemetry simulator and validator are local research utilities. Successful offline checks do not establish that live inference respects 750 ms or browser rendering meets its frame budget; those claims require dedicated timing tests.

## 6. Local documentation commit workflow

After saving this file as docs/WORKFLOW_SPECIFICATION.md, commit and push it with:

~~~bash
git checkout main
git pull --ff-only origin main
mkdir -p docs
# Save the complete contents of this document as docs/WORKFLOW_SPECIFICATION.md
${EDITOR:-vi} docs/WORKFLOW_SPECIFICATION.md
git add docs/WORKFLOW_SPECIFICATION.md
git commit -m "docs: specify real-time workflow and degradation handling"
git push origin main
~~~

Repository remote: https://github.com/Titiksha-P/Cognisynth.git. If direct pushes to main are restricted, push a feature branch and open a pull request.

## 7. Acceptance checklist before claiming conformance

- [ ] Inference timeout is enforced with a monotonic 750 ms deadline across request and parse.
- [ ] Timeout, late response, malformed output, and API exceptions route to fallback.
- [ ] Critical controls remain visible and reachable from an explicitly required home node.
- [ ] Dangling navigation edges are rejected.
- [ ] Density ceiling is enforced and monotonic visual-entropy behavior is implemented if claimed.
- [ ] Runtime mutation is restricted to registered elements.
- [ ] Failure injection demonstrates safe behavior for missing elements and partial mutation.
- [ ] CI checks pass and empirical claims are supported by measured, reproducible results.

## 8. Safety and research boundary

This workflow is a research specification. It is not a safety certification, real-time guarantee, or evidence of clinical/industrial efficacy. Do not deploy in a safety-critical environment without independent hazard analysis, verified recovery behavior, human-factors evaluation, security review, and domain-specific approval.
