# MFG-C2-010 — Unit Tests: inner DomainWorkflowGraph + nested end-to-end
#
# Two layers:
#   (1) Inner-graph construction: DomainWorkflowGraph registers the 6 domain
#       nodes, has the expected name/schema, and shapes get_output() the way the
#       outer merge_output() reads it.
#   (2) Nested end-to-end: the REAL outer agent (MfgC2010Agent / Graph) is driven
#       via AgentBaseGraph.invoke() with a trusted caller context (the backbone
#       trust gate denies ANONYMOUS). The inner workflow runs all 6 nodes
#       and the outer merge_output maps the inner sub_result back, so the backbone
#       routes main -> post_process -> finalize. No LLM.
#
# Per-node classification logic (NORMAL/WARNING/ALERT, cause, action) is asserted
# directly at the unit level (test_anomaly_detection_node / test_cause_hint_node /
# test_action_recommendation_node / test_statistical_analysis_node). The
# end-to-end tests here assert the integration contract the nested run actually
# guarantees: SUCCESS, a populated valid-JSON anomaly document, and backbone
# traversal.
#
# GraphNode boundary contract: BaseGraph.invoke()
# builds a FRESH inner-graph initial state seeded with the extract_input() return
# value (user_input = validated JSON payload) and framework fields only.  Outer
# scalar fields written by PreProcessNode (equipment_id, analysis_window, ...) are
# NOT propagated.  BaselineLoadNode now re-derives equipment_id from the payload
# via _equipment_id_from_payload() -- the same fallback SensorDataIngestionNode
# uses -- so the baseline loads correctly inside the nested run and anomalous
# readings reach the WARNING/ALERT classification path.
#
# Deterministic -- no LLM, no network. framework.* / src.* imports only.

import json
import traceback
import uuid

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.domain_workflow_graph import DomainWorkflowGraph
from src.graph.graph import Graph, AnomalyDetectionGraphNode
from src.schemas.state import State, from_json, to_json


_VALID_FLAGS = {"NORMAL", "WARNING", "ALERT"}
_VALID_ACTIONS = {
    "CONTINUE",
    "INSPECT",
    "REDUCE_LOAD",
    "STOP_EQUIPMENT",
    "ESCALATE_MAINTENANCE",
}


def _payload(readings, equipment_id="CNC-LATHE-014"):
    return json.dumps(
        {
            "equipment_id": equipment_id,
            "analysis_window": {
                "start_time": "2025-01-01T00:00:00Z",
                "end_time": "2025-01-01T01:00:00Z",
                "window_size_minutes": 60,
            },
            "readings": readings,
        }
    )


# Readings well outside the CNC-LATHE-014 baseline (vibration ucl=4.0).
_ANOMALOUS = _payload(
    [
        {"sensor_id": "vibration_mm_s", "value": 9.0, "unit": "mm/s"},
        {"sensor_id": "vibration_mm_s", "value": 9.5, "unit": "mm/s"},
        {"sensor_id": "vibration_mm_s", "value": 10.0, "unit": "mm/s"},
    ]
)

# Readings centred on the baseline mean (vibration mean=2.0).
_NORMAL = _payload(
    [
        {"sensor_id": "vibration_mm_s", "value": 2.0, "unit": "mm/s"},
        {"sensor_id": "vibration_mm_s", "value": 2.1, "unit": "mm/s"},
        {"sensor_id": "vibration_mm_s", "value": 1.9, "unit": "mm/s"},
    ]
)


def _run(user_input: str) -> dict:
    ctx = InvocationContext.for_internal(caller_id="test-suite")
    return Graph().invoke(user_input, ctx=ctx)


def _run_inner(user_input: str) -> dict:
    """Directly invoke DomainWorkflowGraph (inner graph) as GraphNode does.

    Replicates GraphNode.execute(): builds a fresh ctx, calls inner.invoke()
    with user_input = the JSON payload string, and returns the raw sub_result
    from get_output(). Used to isolate whether the inner graph succeeds
    independently of the outer backbone.
    """
    ctx = InvocationContext.for_internal(caller_id="test-suite")
    inner = DomainWorkflowGraph()
    return inner.invoke(user_input, ctx=ctx)


def _build_outer_state_after_preprocess(user_input: str) -> dict:
    """Build a synthetic outer backbone state as it would look after
    InitializeNode + PreProcessNode have run successfully.

    This replicates what the backbone state dict contains when
    AnomalyDetectionGraphNode.execute() is about to be called, so we can
    exercise it in isolation without running the full outer backbone.
    """
    cid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    trid = str(uuid.uuid4())
    return {
        # SubgraphContext / AgentState standard fields (set by BaseGraph.invoke + InitializeNode)
        "user_input": user_input,
        "input_context": {},
        "session_id": sid,
        "correlation_id": cid,
        "trace_id": trid,
        "thread_id": tid,
        "schema_version": "1.4",
        "caller_trust_level": TrustLevel.INTERNAL.value,
        "caller_id": "test-suite",
        "hitl_allowed": True,
        "status": AgentStatus.SUCCESS.value,  # PreProcessNode set this
        "retry_count": 0,
        "hitl_count": 0,
        "node_history": ["InitializeNode", "PreProcessNode"],
        "error_log": [],
        "execution_time": {},
        # Fields PreProcessNode wrote
        "validated_input": user_input,
        "equipment_id": "CNC-LATHE-014",
        "analysis_window": json.dumps(
            {
                "start_time": "2025-01-01T00:00:00Z",
                "end_time": "2025-01-01T01:00:00Z",
                "window_size_minutes": 60,
            }
        ),
        "enriched_context": {"source": "ManufacturingEquipmentAnomalyDetectionAgent", "channel": "unknown"},
    }


class TestInnerGraphConstruction:
    def test_registers_six_domain_nodes(self):
        inner = DomainWorkflowGraph()
        inner.register_nodes()
        assert set(inner._nodes.keys()) == {
            "sensor_data_ingestion",
            "baseline_load",
            "statistical_analysis",
            "anomaly_detection",
            "cause_hint",
            "action_recommendation",
        }

    def test_inner_name_and_schema(self):
        inner = DomainWorkflowGraph()
        assert inner.name == "mfg_c2_010_anomaly_workflow"
        assert inner.state_schema is State

    def test_get_output_shape_matches_merge_contract(self):
        # The keys the outer merge_output() reads back must be present.
        inner = DomainWorkflowGraph()
        out = inner.get_output(
            {
                "anomaly_flag": "ALERT",
                "anomaly_score": 0.9,
                "probable_cause": "bearing_wear",
                "recommended_action": "STOP_EQUIPMENT",
                "anomaly_narrative": "n",
                "status": AgentStatus.SUCCESS.value,
            }
        )
        for key in (
            "output",
            "anomaly_flag",
            "anomaly_score",
            "probable_cause",
            "recommended_action",
            "anomaly_narrative",
            "status",
        ):
            assert key in out
        # "output" is a rendered JSON document carrying the flag.
        assert json.loads(out["output"])["anomaly_flag"] == "ALERT"

    def test_inner_graph_direct_invoke_returns_success(self):
        # Directly invoke the inner DomainWorkflowGraph as GraphNode.execute() does:
        # pass the JSON payload as user_input, trusted context. This isolates
        # the inner graph from the outer backbone.
        sub_result = _run_inner(_ANOMALOUS)
        assert sub_result.get("status") == AgentStatus.SUCCESS.value, f"Inner graph failed. sub_result={sub_result!r}"
        assert (
            sub_result.get("anomaly_flag") in _VALID_FLAGS
        ), f"Unexpected anomaly_flag: {sub_result.get('anomaly_flag')!r}"

    def test_inner_graph_direct_invoke_classifies_alert(self):
        # Anomalous readings (vibration 9-10 mm/s vs ucl=4.0) must classify ALERT.
        sub_result = _run_inner(_ANOMALOUS)
        assert sub_result.get("anomaly_flag") == "ALERT", (
            f"Expected ALERT, got: {sub_result.get('anomaly_flag')!r}. " f"sub_result={sub_result!r}"
        )

    def test_graph_node_execute_with_synthetic_outer_state(self):
        # Directly call AnomalyDetectionGraphNode.__call__() with a synthetic
        # outer state that mirrors what the backbone produces after PreProcessNode.
        # This isolates the GraphNode wrapper from the LangGraph backbone and
        # reveals the exact error (if any) in the graph_node execute() path.
        state = _build_outer_state_after_preprocess(_ANOMALOUS)
        node = AnomalyDetectionGraphNode()
        try:
            result = node(state)  # __call__ = BaseNode.__call__; catches all exceptions
        except Exception as exc:
            pytest.fail(
                f"AnomalyDetectionGraphNode.__call__ raised unexpectedly: "
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            )
        assert result.get("status") == AgentStatus.SUCCESS.value, (
            f"AnomalyDetectionGraphNode returned status={result.get('status')!r}. "
            f"error_log={result.get('error_log', [])!r}. "
            f"Full result={result!r}"
        )


class TestNestedEndToEnd:
    """The nested run completes and surfaces a well-formed anomaly document."""

    def test_invoke_returns_success(self):
        result = _run(_ANOMALOUS)
        assert (
            result.get("status") == AgentStatus.SUCCESS.value
        ), f"Expected success, got {result.get('status')}. result={result!r}"

    def test_output_is_populated_valid_json_document(self):
        output = _run(_ANOMALOUS).get("output")
        assert isinstance(output, str) and output.strip()
        doc = json.loads(output)  # must be valid JSON (not the withheld notice)
        # The rendered structured result always carries these domain keys.
        for key in ("equipment_id", "anomaly_flag", "anomaly_score", "recommended_action"):
            assert key in doc, f"anomaly document missing key: {key}"

    def test_classification_is_a_valid_enum(self):
        doc = json.loads(_run(_ANOMALOUS).get("output", "{}"))
        assert doc["anomaly_flag"] in _VALID_FLAGS
        assert doc["recommended_action"] in _VALID_ACTIONS

    def test_node_history_records_backbone_traversal(self):
        history = _run(_ANOMALOUS).get("node_history", [])
        assert isinstance(history, list)
        # BaseNode.__call__ appends each node's CLASS name (not the slot name).
        for cls_name in ("PreProcessNode", "AnomalyDetectionGraphNode", "PostProcessNode"):
            assert cls_name in history, f"node_history missing {cls_name}: {history}"

    def test_normal_readings_classify_normal(self):
        # Centred-on-baseline readings are unambiguously NORMAL -> CONTINUE.
        result = _run(_NORMAL)
        assert result.get("status") == AgentStatus.SUCCESS.value
        doc = json.loads(result.get("output", "{}"))
        assert doc["anomaly_flag"] == "NORMAL"
        assert doc["recommended_action"] == "CONTINUE"

    def test_unknown_equipment_run_does_not_crash(self):
        # Unknown equipment -> BaselineLoadNode cannot resolve a profile inside
        # the inner graph; the nested run must still surface a terminal status,
        # never raise.
        result = _run(_payload([{"sensor_id": "vibration_mm_s", "value": 5.0}], equipment_id="UNKNOWN-MACHINE-999"))
        assert result.get("status") in (
            AgentStatus.SUCCESS.value,
            AgentStatus.ERROR.value,
        )


class TestStateRoundTrip:
    """State is a flat TypedDict whose structured fields round-trip as JSON
    strings -- exercise the producer/consumer contract."""

    def test_json_string_fields_survive_round_trip(self):
        summary = {"vibration_mm_s": {"mean": 9.5, "stddev": 0.4}}
        # A value written by one node (to_json) is read by the next (from_json).
        assert from_json(to_json(summary)) == summary

    def test_none_field_round_trips_as_none(self):
        # An unset structured field stays distinguishable from an empty container.
        assert to_json(None) is None
        assert from_json(None, {}) == {}
