"""AgentCore Platform v1.0"""

# State must be a flat TypedDict — never a Pydantic BaseModel.  LangGraph
# checkpoints use msgpack serialization; Pydantic objects cause silent
# corruption.  Extend AgentState with agent-specific fields only.  Do NOT add
# credentials, secrets, or Pydantic models.
#
# MFG-C2-010 — Manufacturing Equipment Anomaly Detection Agent
# Two-layer nested Cat 2 graph: outer backbone (AgentBaseGraph) + inner
# domain workflow (BaseGraph).  Fields below cover both layers.
#
# Sensor-data safety: raw sensor time-series arrays are NEVER written to State.
# SensorDataIngestionNode collapses the raw window into per-sensor SUMMARY
# STATISTICS (min/max/mean/stddev/sample_count) before anything reaches State.
# Equipment credentials / connection strings are never persisted either.
#
# msgpack safety: structured fields (dict / list) are stored as JSON STRINGS,
# not bare Python containers — a bare dict/list in a checkpointed State field
# does not survive the round trip.  Producers serialize with to_json() on
# write; consumers deserialize with from_json() on read.  Scalar fields
# (anomaly_score float, anomaly_flag str, ...) are stored directly.

import json
from typing import Any, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> Optional[str]:
    """Serialize a dict/list State field to a JSON string (msgpack safety).

    None passes through unchanged so an 'unset' field stays distinguishable
    from an empty container.
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON-string State field back to its dict/list.

    None / empty / malformed input -> the supplied ``default`` so a missing or
    corrupt field is non-fatal for the consuming node.
    """
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class State(AgentState):
    """Flat TypedDict for MFG-C2-010.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.

    anomaly_flag is one of: "NORMAL" / "WARNING" / "ALERT".
    recommended_action is one of: "CONTINUE" / "INSPECT" / "REDUCE_LOAD" /
        "STOP_EQUIPMENT" / "ESCALATE_MAINTENANCE".
    """

    # ------------------------------------------------------------------
    # Outer layer — set by PreProcessNode / AnomalyDetectionGraphNode.merge_output
    # ------------------------------------------------------------------

    # Schema-validated, PII-free sensor request payload produced by
    # PreProcessNode.  Raw input is NOT persisted beyond PreProcessNode.
    validated_input: Optional[str]

    # JSON STRING (to_json) of the analysis window descriptor:
    # {"start_time": str, "end_time": str, "window_size_minutes": int}.
    analysis_window: Optional[str]

    # Equipment identifier the analysis is scoped to (e.g. "CNC-LATHE-014").
    equipment_id: Optional[str]

    # Formatted outer output consumed by PostProcessNode's output boundary.
    result: Optional[str]

    # Declared detection settings, already parsed and bounded by the outer graph
    # and seeded into the inner graph's initial state.  A node's contract is
    # execute(state) -> dict with no config argument, so this is the only route
    # from config/config.yaml to a domain node.  Scalars only, and never
    # rendered into the report.
    detection_settings: Optional[dict[str, Any]]

    # ------------------------------------------------------------------
    # Inner layer — domain nodes (DomainWorkflowGraph)
    # ------------------------------------------------------------------

    # SensorDataIngestionNode output.
    # JSON STRING (to_json) of per-sensor SUMMARY statistics (NO raw arrays):
    # {sensor_id: {"min": float, "max": float, "mean": float, "stddev": float,
    #              "sample_count": int, "unit": str}}.
    # Consumer (StatisticalAnalysisNode) reads it back via from_json().
    per_sensor_summary: Optional[str]

    # Number of distinct sensors seen in the ingested window.
    sensor_count: Optional[int]

    # BaselineLoadNode output.
    # JSON STRING (to_json) of the per-equipment normal operating profile:
    # {sensor_id: {"mean": float, "stddev": float, "ucl": float, "lcl": float}}.
    # Consumer (StatisticalAnalysisNode) reads it back via from_json().
    baseline_profile: Optional[str]

    # StatisticalAnalysisNode output.
    # JSON STRING (to_json) of per-sensor anomaly scores: {sensor_id: float}.
    per_sensor_scores: Optional[str]

    # Aggregate anomaly score across all sensors (0.0-1.0).
    anomaly_score: Optional[float]

    # AnomalyDetectionNode output — classified status: NORMAL / WARNING / ALERT.
    anomaly_flag: Optional[str]

    # CauseHintNode output — e.g. "bearing_wear", "coolant_temperature_drift".
    probable_cause: Optional[str]

    # ActionRecommendationNode outputs.
    # CONTINUE / INSPECT / REDUCE_LOAD / STOP_EQUIPMENT / ESCALATE_MAINTENANCE.
    recommended_action: Optional[str]

    # Human-readable maintenance-team summary (flag, score, cause, action).
    anomaly_narrative: Optional[str]

    # ------------------------------------------------------------------
    # Tracing / audit — framework-managed; do NOT write from node code
    # ------------------------------------------------------------------

    trace_id: Optional[str]
    correlation_id: Optional[str]
    error_code: Optional[str]
    # node_history inherited from AgentState; listed here for clarity
    # node_history: Optional[List[str]]
