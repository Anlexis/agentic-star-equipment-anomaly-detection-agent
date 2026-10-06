# PB-6 - Invoke-Order Boundary: a full agent.invoke() must execute the fixed
# AgentBaseGraph backbone in order.
#
# The Cat 1 backbone is fixed and is NEVER overridden by a Cat 2 template
# (add_edges() belongs to the framework):
#
#     START -> initialize -> pre_process -> main -> {route} -> post_process
#           -> finalize -> END
#
# The framework records every executed node in `node_history` (an AgentState
# field whose reducer is operator.add, so entries accumulate in execution
# order). Each entry is the node's CLASS NAME - appended by BaseNode.__call__.
#
# For MFG-C2-010 (Cat 2, two-layer nested) the `main` slot is a GraphNode
# subclass (AnomalyDetectionGraphNode) that delegates to the inner
# DomainWorkflowGraph. The inner graph runs with its own state; its inner
# node_history is NOT merged back into the outer state (merge_output() maps
# only result / anomaly_* / status), so the OUTER node_history contains
# exactly the five backbone slots - never the inner domain nodes.
#
# This test drives a real end-to-end Graph().invoke() over a valid domain
# payload and asserts the surfaced node_history matches the canonical backbone
# order. A SUCCESS terminal status is required: on any non-SUCCESS status
# route() short-circuits main -> finalize and the post_process slot is
# skipped, which is itself an invoke-order violation this test would catch.
#
# docs/03_test_spec.md section 4 (PoB).
# Deterministic - no LLM, no network. framework.* / src.* imports only.

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext

from src.graph.graph import Graph


# --- TEMPLATE-SPECIFIC ------------------------------------------------------
# The `main`-slot GraphNode class name for THIS template. A sibling template
# mirroring this canonical changes ONLY this one entry (its own domain
# <...>GraphNode); the other four backbone slot names are framework-fixed.
_MAIN_SLOT_NODE = "AnomalyDetectionGraphNode"

# A valid, PII-free sensor-anomaly payload that drives the full domain workflow
# to a SUCCESS terminal status. Shape mirrors the domain input contract in
# src/nodes/pre_process_node.py + src/nodes/sensor_data_ingestion_node.py: a
# JSON object with equipment_id + an analysis_window + a readings array. Raw
# sensor readings are collapsed to per-sensor SUMMARY statistics by
# SensorDataIngestionNode before anything reaches State - the raw arrays
# themselves never enter State. (Same anomalous
# payload proven to reach SUCCESS end-to-end in test_domain_workflow_graph.py.)
_VALID_PAYLOAD = json.dumps(
    {
        "equipment_id": "CNC-LATHE-014",
        "analysis_window": {
            "start_time": "2025-01-01T00:00:00Z",
            "end_time": "2025-01-01T01:00:00Z",
            "window_size_minutes": 60,
        },
        "readings": [
            {"sensor_id": "vibration_mm_s", "value": 9.0, "unit": "mm/s"},
            {"sensor_id": "vibration_mm_s", "value": 9.5, "unit": "mm/s"},
            {"sensor_id": "vibration_mm_s", "value": 10.0, "unit": "mm/s"},
        ],
    }
)
# --- END TEMPLATE-SPECIFIC --------------------------------------------------

# Canonical AgentBaseGraph backbone execution order, by node class name as
# recorded in node_history. Four entries are framework-fixed; only
# _MAIN_SLOT_NODE is template-specific.
_EXPECTED_ORDER = [
    "InitializeNode",  # framework default  (initialize slot)
    "PreProcessNode",  # template-standard  (pre_process slot, input boundary)
    _MAIN_SLOT_NODE,  # TEMPLATE-SPECIFIC  (main slot GraphNode)
    "PostProcessNode",  # template-standard  (post_process slot, output boundary)
    "FinalizeNode",  # framework default  (finalize slot)
]


def _run() -> dict:
    """Run a full, fully-trusted end-to-end invocation and return the output.

    Uses InvocationContext.for_internal so the backbone trust gate admits the
    call. VERIFIED_EXTERNAL — what the deployed entry point grants — also
    passes; INTERNAL is used here so the assertion holds for both.
    """
    ctx = InvocationContext.for_internal(caller_id="test-suite")
    return Graph().invoke(_VALID_PAYLOAD, ctx=ctx)


class TestInvokeOrderBoundary:
    """PB-6: full agent.invoke() executes the backbone in the fixed order."""

    def test_invoke_reaches_success(self):
        """The full run must terminate SUCCESS - otherwise route() short-circuits
        main -> finalize and the post_process slot never runs."""
        result = _run()
        assert (
            result.get("status") == AgentStatus.SUCCESS.value
        ), f"Expected SUCCESS, got {result.get('status')!r}. result={result!r}"

    def test_output_is_non_empty(self):
        """A successful run must surface a non-empty gated output."""
        assert _run().get("output"), "invoke() surfaced an empty output"

    def test_node_history_is_populated(self):
        """node_history must be a non-empty list of node class-name strings."""
        history = _run().get("node_history")
        assert isinstance(history, list) and history, f"node_history must be a non-empty list, got {history!r}"
        assert all(isinstance(n, str) for n in history), f"node_history entries must be strings, got {history!r}"

    def test_backbone_slot_order(self):
        """Core invoke-order boundary: the pre_process slot runs before the
        domain main slot, which runs before the post_process slot - as a
        strict ordered subsequence of node_history."""
        history = _run().get("node_history", [])
        ordered_slots = ["PreProcessNode", _MAIN_SLOT_NODE, "PostProcessNode"]
        for name in ordered_slots:
            assert name in history, f"Expected backbone slot {name!r} in node_history, got {history!r}"
        positions = [history.index(name) for name in ordered_slots]
        assert positions == sorted(positions), (
            f"Backbone slots executed out of order: {ordered_slots} at {positions}. " f"node_history={history!r}"
        )

    def test_full_backbone_sequence(self):
        """The complete AgentBaseGraph backbone order:
        initialize -> pre_process -> main -> post_process -> finalize."""
        history = _run().get("node_history", [])
        assert history == _EXPECTED_ORDER, (
            "node_history does not match the canonical backbone order.\n"
            f"  expected: {_EXPECTED_ORDER}\n"
            f"  actual:   {history}"
        )
