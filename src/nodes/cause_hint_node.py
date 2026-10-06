"""AgentCore Platform v1.0"""

# MFG-C2-010 — CauseHintNode
# Inner domain node 5: map the top-scoring sensor to a probable mechanical
# cause using a rule table (sensor-group prefix -> cause hint).
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).

import logging
from typing import Any, ClassVar, Dict, List, Tuple

from framework.schemas.agent_status import AgentStatus
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json

logger = logging.getLogger(__name__)

# Sensor-group -> probable-cause rules. A sensor_id matches a rule when it
# contains the substring key (case-insensitive). The table is closed: the cause
# string renders into the report, so it comes from this reviewed vocabulary and
# never from a configured or caller-supplied value.
_CAUSE_RULES: List[Tuple[str, str]] = [
    ("vibration", "bearing_wear"),
    ("vib", "bearing_wear"),
    ("temp", "coolant_temperature_drift"),
    ("coolant", "coolant_temperature_drift"),
    ("current", "spindle_load_spike"),
    ("load", "spindle_load_spike"),
    ("pressure", "hydraulic_pressure_loss"),
]

_CAUSE_UNKNOWN = "undetermined"
_CAUSE_NONE = "none"


def _resolve_cause(sensor_id: str, rules: List[Tuple[str, str]]) -> str:
    """Return the probable cause for a sensor_id, or 'undetermined'."""
    sid = sensor_id.lower()
    for needle, cause in rules:
        if needle in sid:
            return cause
    return _CAUSE_UNKNOWN


class CauseHintNode(FunctionNode):
    """Infer a probable cause from the top-scoring sensor.

    Input state keys:
        per_sensor_scores: JSON string from StatisticalAnalysisNode
        anomaly_flag:      NORMAL / WARNING / ALERT

    Output state keys (partial dict):
        probable_cause: cause string ("none" when NORMAL)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> dict[str, Any]:
        # A reason settled earlier in the run is the real one: pass it through
        # untouched instead of doing work on input that was already declined.
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        rules = _CAUSE_RULES

        anomaly_flag = state.get("anomaly_flag", "NORMAL")
        scores: Dict[str, Any] = from_json(state.get("per_sensor_scores"), {})

        # NORMAL equipment has no actionable cause.
        if anomaly_flag == "NORMAL" or not scores:
            probable_cause = _CAUSE_NONE
            top_sensor_id = ""
        else:
            top_sensor_id = max(scores, key=lambda k: scores.get(k, 0.0))
            top_score = scores.get(top_sensor_id, 0.0)
            if top_score <= 0.0:
                probable_cause = _CAUSE_NONE
            else:
                probable_cause = _resolve_cause(top_sensor_id, rules)

        # Domain audit: cause resolved.
        emit_trace_event(
            "cause_hint_resolved",
            {"probable_cause": probable_cause, "top_sensor_id": top_sensor_id},
            state,
        )

        logger.info(
            "CauseHintNode: flag=%s top_sensor=%s -> cause=%s",
            anomaly_flag,
            top_sensor_id,
            probable_cause,
        )

        return {
            "probable_cause": probable_cause,
        }
