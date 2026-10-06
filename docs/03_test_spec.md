# MFG-C2-010 — Test Specification

**Template ID:** MFG-C2-010
**Name:** ManufacturingEquipmentAnomalyDetectionAgent
**Scope:** unit tests (per node + both graphs), end-to-end scenarios through the real entry
point, and proof-of-boundary tests.

---

## 1. Unit Test Inventory

### 1.1 State (`tests/unit/test_state.py`)

| # | Test | Input scenario | Expected outcome |
|---|---|---|---|
| ST-1 | test_state_is_typeddict | import `State` | subclass of `AgentState`; instantiable as a plain dict |
| ST-2 | test_no_credential_fields | introspect annotations | no field name matches jwt/token/api_key/secret/password/credential; no raw-sensor-array field |

### 1.2 PreProcessNode (`tests/unit/test_pre_process_node.py`) — the caller boundary

| # | Test | Input scenario | Expected outcome |
|---|---|---|---|
| PP-1 | test_empty_input | `user_input=""` | declined: status SUCCESS carrying a reason code; error_log mentions empty |
| PP-2 | test_valid_payload | JSON with equipment_id + readings | status SUCCESS; validated_input set; equipment_id extracted; analysis_window is a JSON string |
| PP-3 | test_oversized_payload | payload > 200000 chars | declined: status SUCCESS carrying a reason code; size violation |
| PP-4 | test_missing_equipment_id | JSON without equipment_id | declined: status SUCCESS carrying a reason code; equipment_id-missing violation |
| PP-5 | test_invalid_equipment_id_format | equipment_id outside `^[A-Za-z0-9._-]{2,64}$` | declined: status SUCCESS carrying a reason code; error_log names equipment_id |

### 1.3 SensorDataIngestionNode (`tests/unit/test_sensor_data_ingestion_node.py`)

| # | Test | Input scenario | Expected outcome |
|---|---|---|---|
| SI-1 | test_normal_payload | 3 sensors x >=3 readings | per_sensor_summary has min/max/mean/stddev/sample_count; sensor_count=3; **no raw array in output** |
| SI-2 | test_malformed_json | validated_input not JSON | declined: status SUCCESS carrying a reason code |
| SI-3 | test_empty_sensors | `readings=[]` | sensor_count=0; per_sensor_summary empty dict (json) |
| SI-4 | test_below_min_samples | sensor with 1 reading, min_samples=3 | summarized (best-effort), flagged in logs, NOT errored |

### 1.4 BaselineLoadNode (`tests/unit/test_baseline_load_node.py`)

| # | Test | Input scenario | Expected outcome |
|---|---|---|---|
| BL-1 | test_known_equipment | equipment_id with a built-in/file baseline | baseline_profile populated (json) |
| BL-2 | test_unknown_equipment | equipment_id with no baseline | declined: status SUCCESS carrying a reason code; error_log mentions no baseline |
| BL-3 | test_missing_equipment_id | equipment_id absent from state — a broken upstream invariant, not caller data | status ERROR; error_log names equipment_id |
| BL-4 | test_missing_baseline_file_path_falls_back_to_builtin | baseline path points nowhere, KNOWN id | the built-in profile is the fallback; no stop at all |

### 1.5 StatisticalAnalysisNode (`tests/unit/test_statistical_analysis_node.py`)

| # | Test | Input scenario | Expected outcome |
|---|---|---|---|
| SA-1 | test_normal_readings | summaries approx baseline | anomaly_score near 0.0 |
| SA-2 | test_single_anomalous_sensor | one sensor mean far from baseline | per_sensor_scores has one high score; anomaly_score elevated |
| SA-3 | test_multi_sensor_anomaly | multiple sensors deviating | anomaly_score high (worst-blended) |
| SA-4 | test_missing_sensor_baseline | summary sensor absent from baseline | score 0.0 for it; no exception |
| SA-5 | test_boundary_score_range | extreme deviation | anomaly_score clamped within [0.0, 1.0] |

### 1.6 AnomalyDetectionNode (`tests/unit/test_anomaly_detection_node.py`)

| # | Test | Input scenario | Expected outcome |
|---|---|---|---|
| AD-1 | test_normal | score < warning (0.4) | anomaly_flag NORMAL |
| AD-2 | test_warning | warning <= score < alert | anomaly_flag WARNING |
| AD-3 | test_alert | score >= alert (0.7) | anomaly_flag ALERT; interim narrative set |
| AD-4 | test_boundary | score exactly = warning / = alert | flag follows `>=` boundary (WARNING at 0.4, ALERT at 0.7) |

### 1.7 CauseHintNode (`tests/unit/test_cause_hint_node.py`)

| # | Test | Input scenario | Expected outcome |
|---|---|---|---|
| CH-1 | test_known_pattern | top sensor id contains "vibration", flag WARNING | probable_cause = bearing_wear |
| CH-2 | test_unknown_sensor | top sensor id with no rule match, flag WARNING | probable_cause = undetermined |
| CH-3 | test_no_anomaly | flag NORMAL | probable_cause = none |

### 1.8 ActionRecommendationNode (`tests/unit/test_action_recommendation_node.py`)

| # | Test | Input scenario | Expected outcome |
|---|---|---|---|
| AR-1 | test_normal | flag NORMAL | recommended_action CONTINUE |
| AR-2 | test_warning | flag WARNING, cause spindle_load_spike | recommended_action REDUCE_LOAD; narrative non-empty |
| AR-3 | test_alert | flag ALERT, cause bearing_wear | recommended_action STOP_EQUIPMENT; narrative non-empty |
| AR-4 | test_unknown_cause_fallback | flag ALERT, cause undetermined | recommended_action ESCALATE_MAINTENANCE |

### 1.9 PostProcessNode (`tests/unit/test_post_process_node.py`) — the output boundary

| # | Test | Input scenario | Expected outcome |
|---|---|---|---|
| PO-1 | test_normal_output | clean result string | status SUCCESS; formatted_output == result |
| PO-2 | test_credential_in_result_is_withheld | result contains a simulated `password=...` assignment | status ERROR; every output-bearing field present in the delta and cleared; a non-empty withheld notice in formatted_output |
| PO-3 | test_empty_passthrough | result empty | status SUCCESS; non-fatal |

### 1.10 Graphs

| File | Test | Expected outcome |
|---|---|---|
| `test_domain_workflow_graph.py` | DW-1 smoke | invoke inner graph with a valid fixture state; `anomaly_flag` present in `get_output()` |
| `test_graph.py` | G-1 smoke | outer graph composition; `merge_output` maps inner `output` -> outer `result` |
| `test_get_output_containment.py` | GO-1..GO-6 | on a non-success status the surfaced value never falls back to `result`; a withheld notice still reaches the caller; the success path is unchanged |

## 2. End-to-End Scenarios (`tests/proof_of_boundary/test_invoke_e2e.py`)

Driven through the real ASGI `POST /invoke`, so every case crosses the entry-point auth, the
trust gate, the input boundary, the inner workflow, and the output boundary.

| # | Scenario | Fixture | Expected |
|---|---|---|---|
| E2E-1 | end-to-end NORMAL | readings on the baseline | anomaly_flag NORMAL, recommended_action CONTINUE, anomaly_score 0.0 |
| E2E-2 | end-to-end WARNING | readings mildly deviating | anomaly_flag WARNING, action INSPECT |
| E2E-3 | end-to-end ALERT | readings strongly deviating | anomaly_flag ALERT, action STOP_EQUIPMENT, cause bearing_wear |
| E2E-4 | unknown equipment | equipment_id with no baseline | declined: status success, the body is the sentence naming what to correct, and NO report — the partially built document is never surfaced as a clean bill of health |
| E2E-5 | entry-point auth | missing / wrong / non-ASCII Bearer | HTTP 401, generic body |
| E2E-6 | declared settings are live | thresholds and saturation varied | the same readings are reclassified, proving `config/config.yaml` reaches the inner nodes |
| E2E-7a | declined request | empty / whitespace input, non-JSON, missing or malformed equipment_id, NaN / Infinity fields, a malformed `input_context.channel` | status success, the body is the sentence naming what to correct, no `anomaly_flag` anywhere in it |
| E2E-7b | refused request | a control token / instruction override in the payload | status error, no output |
| E2E-8 | credential-shaped input | framework-recognised shapes on `input` and on `input_context.channel` | HTTP 400 naming the field, never echoing the value |
| E2E-9 | output-boundary containment | a drifted renderer emitting an undeclared field | status error, no part of the document released, no traceback or source path in the envelope |
| E2E-10 | clean-path control | a valid request | status success, the real report, `PostProcessNode` in `node_history` |

## 3. Proof-of-Boundary Test Scenarios (`tests/proof_of_boundary/`)

| File | Verifies |
|---|---|
| `test_import_isolation.py` | no `src/` file imports a platform-internal module |
| `test_state_safety.py` | `State` has no credential-named fields and no prohibited types (`BaseModel`, `InvocationContext`); confirms raw sensor arrays are not modeled as State fields |
| `test_domain_boundary.py` | raw sensor readings never reach State or the rendered report |
| `test_input_boundary.py` | `PreProcessNode.execute()` called DIRECTLY stops on control tokens, spliced and escaped directives, non-finite and out-of-range numbers, non-inert identifiers and oversized payloads — terminating on the instruction-override sites and declining (SUCCESS + reason code) on the caller-correctable ones; never echoes a rejected value; accepts ordinary maintenance text |
| `test_output_boundary.py` | `PostProcessNode.execute()` withholds on every content and schema violation, clears every output-bearing field (presence AND emptiness), keeps the notice non-empty, and returns a delta the framework's own scan finds clean |
| `test_invoke_e2e.py` | the scenarios in §2 |
| `test_pb_invoke_order.py` | a full `invoke()` runs the fixed backbone in order |
| `test_pb7_hitl_interrupt_propagation.py` | interrupt propagation across the graph boundary |

## 4. Gate / CI Coverage

The repository's own gate scripts under `scripts/` run in CI alongside the pytest tree —
cat-consistency, dependency pinning, stub tests, manifest schema, trust level, audit trace,
credential scan, forbidden strings and OSS licence. Green-on-arrival is required before a
merge request is opened.
