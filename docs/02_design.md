# MFG-C2-010 — Design Specification

**Template ID:** MFG-C2-010
**Name:** ManufacturingEquipmentAnomalyDetectionAgent
**Category:** Cat 2 (multi-step domain workflow)
**Industry:** MFG (Manufacturing)
**Pattern:** two-layer nested graph (outer `AgentBaseGraph` + inner `BaseGraph`)

---

## 1. Position in the framework

| Aspect | Value |
|---|---|
| L1 Base (framework base class) | `AgentBaseGraph` — direct framework inheritance |

- **Nested pattern:** the fixed 5-slot outer backbone is preserved unchanged. All domain
  complexity is encapsulated in a `GraphNode` placed in the `main` slot, which delegates to an
  inner `DomainWorkflowGraph` (`BaseGraph`) with a fully custom linear topology.
- **Import isolation:** no platform-internal imports anywhere in `src/`. Only `framework.*`,
  `langgraph`, `shared.utils.audit_logger`, and `src.*` are imported.

```
framework (AgentBaseGraph, BaseGraph, GraphNode, FunctionNode)   <- inherit here
--------------------------------------------------------------------------
MFG-C2-010 outer graph (MfgC2010Agent)        <- direct framework inheritance
  main slot: AnomalyDetectionGraphNode (GraphNode)
       inner: DomainWorkflowGraph (BaseGraph, 6 domain nodes)
```

## 2. Node Configuration

### Outer backbone (5 fixed slots)

| Slot | Class | Responsibility | Trust level | Boundary |
|---|---|---|---|---|
| initialize | `InitializeNode` (framework default) | schema_version, session_id, trust_level | — | — |
| pre_process | `PreProcessNode` | The caller boundary: parse and validate the request, screen it, extract `equipment_id` + `analysis_window`, write `validated_input`. Raw sensor arrays never stored. | `VERIFIED_EXTERNAL` | Input |
| main | `AnomalyDetectionGraphNode` (`GraphNode`) | Delegates to `DomainWorkflowGraph`; `extract_input` / `merge_output` bridge outer↔inner state; forwards the declared settings | `ANONYMOUS` (unreachable except through pre_process) | error_strategy = propagate |
| post_process | `PostProcessNode` | The output boundary: check the assembled report, clear it on a violation, surface `formatted_output` | `ANONYMOUS` — deliberately, so the boundary is never the first thing skipped on a low-trust call | Output |
| finalize | `FinalizeNode` (framework default) | response_metadata, total_time_ms | — | — |

### Inner domain nodes (`DomainWorkflowGraph`, 6 nodes, linear)

| # | Node | Input (State) | Output (State) | Audit event |
|---|---|---|---|---|
| 1 | `SensorDataIngestionNode` | validated_input | per_sensor_summary, sensor_count, equipment_id, per_sensor_scores(empty) | sensor_data_ingested |
| 2 | `BaselineLoadNode` | equipment_id | baseline_profile (declines on unknown equipment — §Two ways to stop) | baseline_loaded |
| 3 | `StatisticalAnalysisNode` | per_sensor_summary, baseline_profile | per_sensor_scores, anomaly_score | statistical_analysis_complete |
| 4 | `AnomalyDetectionNode` | anomaly_score | anomaly_flag, (interim) anomaly_narrative | anomaly_classified |
| 5 | `CauseHintNode` | per_sensor_scores, anomaly_flag | probable_cause | cause_hint_resolved |
| 6 | `ActionRecommendationNode` | anomaly_flag, probable_cause, anomaly_score | recommended_action, anomaly_narrative | action_recommended |

## 3. Data Flow

```
                      OUTER BACKBONE (AgentBaseGraph)
  user_input
     |
     v
  initialize -> pre_process -> main ------------------> post_process -> finalize -> formatted_output
             (input boundary)  |                      (output boundary)
                                v
              INNER: DomainWorkflowGraph (BaseGraph)
   START
     |
     v
  sensor_data_ingestion -> baseline_load -> statistical_analysis
     |                                              |
     |                                              v
     +----------------------------> anomaly_detection -> cause_hint -> action_recommendation -> END
                                                                                  |
                              get_output() -> {"output": <json>, anomaly_flag, ...}
                                                                                  |
                              merge_output() maps "output" -> outer state["result"]
```

Inner→outer coupling: `DomainWorkflowGraph.get_output()` emits `output` (a deterministic JSON
document of the anomaly result) plus `equipment_id`; `AnomalyDetectionGraphNode.merge_output()`
maps `output` to the outer `result`, which `PostProcessNode` checks and surfaces as
`formatted_output`.

The framework builds a FRESH initial state for the inner graph, seeded only with the string
`extract_input()` returns plus the standard framework fields — outer scalars do not cross the
boundary. `SensorDataIngestionNode` therefore re-derives `equipment_id` from the validated
payload and re-validates it, and the settings the outer graph read from `config/config.yaml` are
seeded through `DomainWorkflowGraph._extra_initial_state()`.

### Two ways to stop

A run that produces no report stops in one of two ways, and the caller can act on only one of
them. The branch is chosen by the call site, never by reading the message text.

**Declined (`status=success`)** — a value the caller can correct. The run **completes** carrying a
sentence that names what to correct (`src/services/failure_message.py`), so a corrected request
can be sent on the same conversation; terminating instead would end the calling surface's turn and
leave the reason reachable from the audit trail alone. This covers every bound in the caller
contract — an empty payload, a payload that is not a JSON object, a missing or malformed
`equipment_id`, `readings` that are not a list or exceed the entry limit, a malformed readings
entry (`sensor_id`, value, `unit`), more than the permitted distinct sensors, a malformed
`analysis_window`, a malformed `input_context` — and, in the inner workflow,
an `equipment_id` with no baseline profile available (`BaselineLoadNode`).

A declined run carries an internal marker onward: the main slot skips the inner workflow when the
marker was settled at the boundary, each inner node returns untouched rather than reporting its
own precondition failure, and `PostProcessNode` renders the reason as the caller-facing body. No
anomaly verdict is produced on this path — a declined request never comes back as NORMAL.

**Refused (`status=error`)** — the run terminates, because a reworded request is not the remedy
and must not be presented as one: instruction-override content in `user_input` or anywhere in the
decoded request payload (`PreProcessNode`, the two sites that pass an empty code). An absent
`equipment_id` where an upstream node was required to have written one likewise terminates — a
broken invariant, not caller data.

### Output schema

The report is an AGGREGATE document. It carries exactly these fields, and the output boundary
refuses a document with any other shape:

| Field | Source |
|---|---|
| equipment_id | the caller's identifier, restricted to `[A-Za-z0-9._-]{2,64}` |
| anomaly_flag | NORMAL / WARNING / ALERT |
| anomaly_score | aggregate score in [0.0, 1.0] |
| probable_cause | one of the reviewed cause vocabulary |
| recommended_action | CONTINUE / INSPECT / REDUCE_LOAD / STOP_EQUIPMENT / ESCALATE_MAINTENANCE |
| narrative | assembled from the four fields above |

Individual readings and the per-sensor statistics computed from them are never rendered. The
template renders no monetary values, so no currency-precision invariant applies; the invariant
this boundary enforces is the aggregate-only schema above, plus the content checks in §5.

## 4. State Definition

Flat `TypedDict` extending `AgentState` (msgpack safety — structured fields stored as JSON strings via `to_json` / `from_json`; scalars stored directly). No credential / PII / raw-sensor-array fields.

| Field | Type | Layer | Set by |
|---|---|---|---|
| validated_input | str | outer | PreProcessNode |
| equipment_id | str | outer | PreProcessNode |
| analysis_window | str (json) | outer | PreProcessNode |
| per_sensor_summary | str (json) | inner | SensorDataIngestionNode |
| sensor_count | int | inner | SensorDataIngestionNode |
| baseline_profile | str (json) | inner | BaselineLoadNode |
| per_sensor_scores | str (json) | inner | StatisticalAnalysisNode |
| anomaly_score | float | inner | StatisticalAnalysisNode |
| anomaly_flag | str | inner | AnomalyDetectionNode |
| probable_cause | str | inner | CauseHintNode |
| recommended_action | str | inner | ActionRecommendationNode |
| anomaly_narrative | str | inner | AnomalyDetectionNode / ActionRecommendationNode |
| detection_settings | dict | inner | seeded from `config/config.yaml` by the inner graph |
| result | str | outer | merge_output -> PostProcessNode |
| trace_id, correlation_id | str | tracing | framework-managed |

`anomaly_flag` is one of {NORMAL, WARNING, ALERT}. `recommended_action` is one of {CONTINUE, INSPECT, REDUCE_LOAD, STOP_EQUIPMENT, ESCALATE_MAINTENANCE}.

## 5. Framework Utilization

- **FunctionNode** — every node extends it; `execute(self, state) -> dict` returns only changed
  keys (partial dict); `AgentStatus` enum values, never bare strings. The framework calls
  `execute` with the state alone, so a node never receives a per-invocation config argument —
  declared settings travel on the state.
- **TrustLevel** — `PreProcessNode` requires `VERIFIED_EXTERNAL` and is the caller boundary; the
  inner domain nodes require the same, matching the manifest's declared
  `required_trust_level`; `PostProcessNode` requires `ANONYMOUS` so the output boundary always
  runs. The standalone entry point in `src/api/server.py` grants `VERIFIED_EXTERNAL` to a caller
  presenting the deployment's `INVOKE_AUTH_TOKEN` as a Bearer token.
- **GraphNode** — `AnomalyDetectionGraphNode` implements `get_subgraph` / `extract_input` /
  `merge_output`, with `error_strategy = "propagate"`.
- **BaseGraph** — `DomainWorkflowGraph` implements all 7 ABC methods (`name`, `state_schema`,
  `_validate_config`, `register_nodes`, `add_edges`, `route`, `get_output`) plus
  `_extra_initial_state()`; `register_nodes()` does **not** call `super()`.
- **Output resolution** — `MfgC2010Agent.get_output()` overrides the inherited
  `formatted_output or result` resolution: on a non-success status the fallback to `result` is
  dropped, so a run that never reached the output boundary surfaces nothing.
- **Audit** — `emit_trace_event(name, payload, state)` is emitted by every domain node and both
  boundaries.

## 6. Composition Pattern

Cat 2 outer `AgentBaseGraph` + `GraphNode` (`AnomalyDetectionGraphNode`) in the `main` slot wrapping inner `BaseGraph` (`DomainWorkflowGraph`). The outer `add_edges()` is **not** overridden; the inner graph owns its own linear topology. The outer class exposes a `Graph` alias (`Graph = MfgC2010Agent`) so `config/agent.yaml` and
`src/api/server.py` (`from src.graph.graph import Graph`) both resolve.

## 7. Design Decision Record

| # | Decision | Rationale |
|---|---|---|
| DDR-1 | Outer graph inherits `AgentBaseGraph` directly | The framework base class carries the fixed backbone; there is no intermediate agent class to inherit. |
| DDR-2 | Inner graph parent = `BaseGraph` (not `AgentBaseGraph`) | The inner workflow needs a fully custom 6-node linear topology with no forced initialize/pre/main/post/finalize backbone. |
| DDR-3 | `error_strategy = "propagate"` on the GraphNode | Anomaly detection is fail-fast: an inner failure must surface, never as a silently degraded "NORMAL" result. A caller-correctable value (an unknown `equipment_id`) is not an inner failure — it stops the run through the declined path in §Two ways to stop, which likewise never produces a NORMAL verdict. |
| DDR-4 | Raw sensor time-series arrays excluded from State; only per-sensor summary statistics stored | Checkpoint size + data minimisation; downstream nodes only need min/max/mean/stddev/count, never the raw window. |
| DDR-5 | Structured State fields stored as JSON strings (`to_json`/`from_json`) | msgpack safety — a bare dict/list in a checkpointed field does not survive the round trip. |
| DDR-6 | Linear inner topology (no conditional branching) | cause_hint / action_recommendation are no-ops for NORMAL; a single linear path keeps the graph deterministic and testable. `route()` is implemented to satisfy the ABC but unused at runtime. |
| DDR-7 | Runtime parameters live in `config/config.yaml`, not in the manifest | `config/agent.yaml` is the registration manifest and carries no runtime block, so a value placed there is read by nothing. The graph is constructed with the file's contents in both the registry and standalone deployments. |
| DDR-8 | The output boundary uses the framework's own credential detector, extended with equipment shapes | The framework re-scans every value a node returns and raises on a credential it recognises; the node wrapper then discards the node's whole delta, clearing included. A locally narrower pattern set would therefore be a containment bypass rather than a gap. |
