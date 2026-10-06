"""AgentCore Platform v1.0"""

# MFG-C2-010 — StatisticalAnalysisNode
# Inner domain node 3: compute a per-sensor anomaly score from the ingested
# summary statistics vs. the equipment baseline, then aggregate to a single
# anomaly_score in [0.0, 1.0].
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).

import logging
from typing import Any, ClassVar, Dict, List

from framework.schemas.agent_status import AgentStatus
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

# A z-score at or above this magnitude maps to a per-sensor score of 1.0.
# Overridden by the declared `anomaly.zscore_saturation`.
_DEFAULT_ZSCORE_SATURATION = 4.0


def _zscore_component(sensor_mean: float, base_mean: float, base_stddev: float, saturation: float) -> float:
    """Normalised |z-score| component, clamped to [0.0, 1.0].

    A zero / negligible baseline stddev means any deviation is maximally
    anomalous; an exact match is 0.0.  ``saturation`` is the z-score magnitude
    that maps to a full 1.0.
    """
    if base_stddev <= 1e-9:
        return 0.0 if abs(sensor_mean - base_mean) <= 1e-9 else 1.0
    z = abs(sensor_mean - base_mean) / base_stddev
    return min(z / saturation, 1.0)


def _violation_component(summary: Dict[str, Any], base: Dict[str, Any]) -> float:
    """Control-limit violation component in [0.0, 1.0].

    Uses the ingested min/max as a proxy for out-of-band excursions against the
    baseline UCL/LCL. 1.0 if either bound is breached, else 0.0.
    """
    ucl = base.get("ucl")
    lcl = base.get("lcl")
    s_max = summary.get("max")
    s_min = summary.get("min")
    breached = False
    if isinstance(ucl, (int, float)) and isinstance(s_max, (int, float)) and s_max > ucl:
        breached = True
    if isinstance(lcl, (int, float)) and isinstance(s_min, (int, float)) and s_min < lcl:
        breached = True
    return 1.0 if breached else 0.0


class StatisticalAnalysisNode(FunctionNode):
    """Compute per-sensor + aggregate anomaly scores.

    Input state keys:
        per_sensor_summary: JSON string from SensorDataIngestionNode
        baseline_profile:   JSON string from BaselineLoadNode

    Output state keys (partial dict):
        per_sensor_scores: JSON string of {sensor_id: score(0..1)}
        anomaly_score:     aggregate score (float, 0..1)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> dict[str, Any]:
        # A reason settled earlier in the run is the real one: pass it through
        # untouched instead of doing work on input that was already declined.
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        settings: Dict[str, Any] = state.get("detection_settings") or {}
        saturation = float(settings.get("zscore_saturation", _DEFAULT_ZSCORE_SATURATION))

        summaries: Dict[str, Any] = from_json(state.get("per_sensor_summary"), {})
        baseline: Dict[str, Any] = from_json(state.get("baseline_profile"), {})

        per_sensor_scores: Dict[str, float] = {}
        for sensor_id, summary in summaries.items():
            if not isinstance(summary, dict):
                per_sensor_scores[sensor_id] = 0.0
                continue
            base = baseline.get(sensor_id)
            if not isinstance(base, dict):
                # No baseline for this sensor — cannot score; treat as normal.
                logger.warning("StatisticalAnalysisNode: no baseline for sensor %s", sensor_id)
                per_sensor_scores[sensor_id] = 0.0
                continue

            # Both detection components always run; the worse of the two drives
            # the sensor's score.
            components: List[float] = [
                _zscore_component(
                    float(summary.get("mean", 0.0)),
                    float(base.get("mean", 0.0)),
                    float(base.get("stddev", 0.0)),
                    saturation,
                ),
                _violation_component(summary, base),
            ]

            score = max(components)
            per_sensor_scores[sensor_id] = round(min(max(score, 0.0), 1.0), 6)

        # Aggregate: the worst-offending sensor drives the equipment-level score,
        # blended with the mean so widespread mild deviation still registers.
        if per_sensor_scores:
            values = list(per_sensor_scores.values())
            worst = max(values)
            avg = sum(values) / len(values)
            anomaly_score = round(min(0.7 * worst + 0.3 * avg, 1.0), 6)
        else:
            anomaly_score = 0.0

        # Domain audit: statistical analysis complete.
        emit_trace_event(
            "statistical_analysis_complete",
            {"anomaly_score": anomaly_score, "scored_sensors": len(per_sensor_scores)},
            state,
        )

        logger.info(
            "StatisticalAnalysisNode: aggregate anomaly_score=%.4f over %d sensors",
            anomaly_score,
            len(per_sensor_scores),
        )

        return {
            "per_sensor_scores": to_json(per_sensor_scores),
            "anomaly_score": anomaly_score,
        }
