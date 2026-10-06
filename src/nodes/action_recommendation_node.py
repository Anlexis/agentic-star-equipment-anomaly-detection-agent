"""AgentCore Platform v1.0"""

# MFG-C2-010 — ActionRecommendationNode
# Inner domain node 6 (last): map (anomaly_flag, probable_cause) to a
# recommended maintenance action and render the maintenance-team narrative.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).

import logging
from typing import Any, ClassVar

from framework.schemas.agent_status import AgentStatus
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

# Recommended-action vocabulary.
_ACT_CONTINUE = "CONTINUE"
_ACT_INSPECT = "INSPECT"
_ACT_REDUCE_LOAD = "REDUCE_LOAD"
_ACT_STOP = "STOP_EQUIPMENT"
_ACT_ESCALATE = "ESCALATE_MAINTENANCE"

# Causes that, at WARNING level, warrant load reduction rather than a passive
# inspection (load/thermal stress that worsens under continued operation).
_WARNING_REDUCE_LOAD_CAUSES = {
    "spindle_load_spike",
    "coolant_temperature_drift",
    "hydraulic_pressure_loss",
}

# Causes that, at ALERT level, warrant a full stop rather than escalation
# (imminent mechanical-failure risk).
_ALERT_STOP_CAUSES = {
    "bearing_wear",
    "hydraulic_pressure_loss",
}


def _resolve_action(anomaly_flag: str, probable_cause: str) -> str:
    """Map (flag, cause) -> a recommended action from the priority table."""
    if anomaly_flag == "ALERT":
        if probable_cause in _ALERT_STOP_CAUSES:
            return _ACT_STOP
        return _ACT_ESCALATE
    if anomaly_flag == "WARNING":
        if probable_cause in _WARNING_REDUCE_LOAD_CAUSES:
            return _ACT_REDUCE_LOAD
        return _ACT_INSPECT
    # NORMAL or any unknown flag -> safe default.
    return _ACT_CONTINUE


def _render_narrative(anomaly_flag: str, anomaly_score: float, probable_cause: str, action: str) -> str:
    """Render a concise maintenance-team summary."""
    if anomaly_flag == "NORMAL":
        return (
            f"Equipment operating within normal parameters "
            f"(anomaly score {anomaly_score:.3f}). Recommended action: {action}."
        )
    return (
        f"{anomaly_flag}: anomaly score {anomaly_score:.3f}. "
        f"Probable cause: {probable_cause}. "
        f"Recommended action: {action}."
    )


class ActionRecommendationNode(FunctionNode):
    """Map (flag, cause) to an action and render the narrative.

    Input state keys:
        anomaly_flag:   NORMAL / WARNING / ALERT
        probable_cause: from CauseHintNode
        anomaly_score:  aggregate score (for the narrative)

    Output state keys (partial dict):
        recommended_action: CONTINUE / INSPECT / REDUCE_LOAD / STOP_EQUIPMENT /
                            ESCALATE_MAINTENANCE
        anomaly_narrative:  maintenance-team summary (non-empty for WARNING/ALERT)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> dict[str, Any]:
        # A reason settled earlier in the run is the real one: pass it through
        # untouched instead of doing work on input that was already declined.
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        anomaly_flag = state.get("anomaly_flag", "NORMAL")
        probable_cause = state.get("probable_cause", "none")

        raw_score = state.get("anomaly_score", 0.0)
        try:
            anomaly_score = float(raw_score) if raw_score is not None else 0.0
        except (TypeError, ValueError):
            anomaly_score = 0.0

        recommended_action = _resolve_action(anomaly_flag, probable_cause)
        anomaly_narrative = _render_narrative(anomaly_flag, anomaly_score, probable_cause, recommended_action)

        # Domain audit: action recommended.
        emit_trace_event(
            "action_recommended",
            {"recommended_action": recommended_action, "anomaly_flag": anomaly_flag},
            state,
        )

        logger.info(
            "ActionRecommendationNode: flag=%s cause=%s -> action=%s",
            anomaly_flag,
            probable_cause,
            recommended_action,
        )

        return {
            "recommended_action": recommended_action,
            "anomaly_narrative": anomaly_narrative,
        }
