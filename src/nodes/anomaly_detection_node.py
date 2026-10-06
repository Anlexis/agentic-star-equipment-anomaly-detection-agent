"""AgentCore Platform v1.0"""

# MFG-C2-010 — AnomalyDetectionNode
# Inner domain node 4: classify the equipment status (NORMAL / WARNING / ALERT)
# from the aggregate anomaly_score against config thresholds.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).

import logging
from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

# Default classification thresholds. Overridden by the declared
# `anomaly.warning_threshold` / `anomaly.alert_threshold`.
_DEFAULT_WARNING_THRESHOLD = 0.4
_DEFAULT_ALERT_THRESHOLD = 0.7

_FLAG_NORMAL = "NORMAL"
_FLAG_WARNING = "WARNING"
_FLAG_ALERT = "ALERT"


class AnomalyDetectionNode(FunctionNode):
    """Classify the anomaly flag from the aggregate score.

    Input state keys:
        anomaly_score: aggregate score (float, 0..1) from StatisticalAnalysisNode

    Output state keys (partial dict):
        anomaly_flag:      NORMAL / WARNING / ALERT
        anomaly_narrative: interim placeholder on ALERT (final narrative is
                           written by ActionRecommendationNode)
        status:            AgentStatus.SUCCESS unconditionally
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> dict[str, Any]:
        # A reason settled earlier in the run is the real one: pass it through
        # untouched instead of doing work on input that was already declined.
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        settings: Dict[str, Any] = state.get("detection_settings") or {}
        warning_threshold = float(settings.get("warning_threshold", _DEFAULT_WARNING_THRESHOLD))
        alert_threshold = float(settings.get("alert_threshold", _DEFAULT_ALERT_THRESHOLD))

        raw_score = state.get("anomaly_score", 0.0)
        try:
            anomaly_score = float(raw_score) if raw_score is not None else 0.0
        except (TypeError, ValueError):
            anomaly_score = 0.0

        if anomaly_score >= alert_threshold:
            anomaly_flag = _FLAG_ALERT
        elif anomaly_score >= warning_threshold:
            anomaly_flag = _FLAG_WARNING
        else:
            anomaly_flag = _FLAG_NORMAL

        result: Dict[str, Any] = {
            "anomaly_flag": anomaly_flag,
            "status": AgentStatus.SUCCESS.value,
        }
        if anomaly_flag == _FLAG_ALERT:
            result["anomaly_narrative"] = (
                f"ALERT: aggregate anomaly score {anomaly_score:.3f} exceeded the "
                f"alert threshold {alert_threshold:.3f}. Investigating probable cause."
            )

        # Domain audit: classification result.
        emit_trace_event(
            "anomaly_classified",
            {"anomaly_flag": anomaly_flag, "anomaly_score": anomaly_score},
            state,
        )

        logger.info(
            "AnomalyDetectionNode: score=%.4f -> flag=%s (warn=%.2f alert=%.2f)",
            anomaly_score,
            anomaly_flag,
            warning_threshold,
            alert_threshold,
        )

        return result
