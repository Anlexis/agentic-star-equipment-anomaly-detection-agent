# MFG-C2-010 -- Diagnostic: stream outer backbone to capture per-node state
#
# Uses LangGraph's stream() to capture the state update after EACH node in the
# outer backbone. This is the only way to observe the exact state dict that
# AnomalyDetectionGraphNode receives from LangGraph (i.e. after input_schema
# filtering) and the exact dict it returns, WITHOUT calling the node directly
# (which bypasses LangGraph's filtering).
#
# Run ONLY when diagnosing the outer-backbone vs direct-call discrepancy.
# Keep in tests/unit/ so it runs in CI and its output appears in --tb=short.

import json
import pprint
import traceback

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext

from src.graph.graph import Graph


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


_ANOMALOUS = _payload(
    [
        {"sensor_id": "vibration_mm_s", "value": 9.0, "unit": "mm/s"},
        {"sensor_id": "vibration_mm_s", "value": 9.5, "unit": "mm/s"},
        {"sensor_id": "vibration_mm_s", "value": 10.0, "unit": "mm/s"},
    ]
)


class TestOuterBackboneStream:
    """Capture per-node state via LangGraph stream() to diagnose the
    outer-backbone failure when AnomalyDetectionGraphNode returns status=error
    despite the inner graph succeeding."""

    def test_stream_captures_anomaly_detection_node_update(self):
        ctx = InvocationContext.for_internal(caller_id="test-suite")
        graph = Graph()
        graph.compile()

        # Collect all events
        events = []
        try:
            for event in graph._compiled.stream(
                {
                    "user_input": _ANOMALOUS,
                    "input_context": {},
                    "session_id": ctx.session_id,
                    "correlation_id": ctx.correlation_id,
                    "trace_id": "test-trace",
                    "thread_id": ctx.thread_id,
                    "schema_version": "1.4",
                    "caller_trust_level": ctx.caller_trust_level.value,
                    "caller_id": ctx.caller_id,
                    "hitl_allowed": ctx.hitl_allowed,
                    "status": "pending",
                    "retry_count": 0,
                    "hitl_count": 0,
                    "node_history": [],
                    "error_log": [],
                    "execution_time": {},
                },
                stream_mode="updates",
            ):
                events.append(event)
        except Exception as exc:
            pytest.fail(f"stream() raised: {type(exc).__name__}: {exc}\n" f"{traceback.format_exc()}")

        # Print all events for diagnostics (visible in --tb=short on failure)
        node_events = {}
        for ev in events:
            for node_name, update in ev.items():
                node_events[node_name] = update

        # Find the AnomalyDetectionGraphNode event
        adn_update = node_events.get("main", {})

        assert adn_update.get("status") == AgentStatus.SUCCESS.value, (
            f"AnomalyDetectionGraphNode (main slot) returned status="
            f"{adn_update.get('status')!r}.\n"
            f"error_log={adn_update.get('error_log', [])!r}\n"
            f"Full main-slot update: {pprint.pformat(adn_update)}\n"
            f"All events:\n{pprint.pformat(node_events)}"
        )
