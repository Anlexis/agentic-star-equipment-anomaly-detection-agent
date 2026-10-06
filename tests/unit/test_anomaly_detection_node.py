# MFG-C2-010 — Unit Tests: AnomalyDetectionNode (inner domain node 4)
#
# Classifies the equipment status from the aggregate anomaly_score against the
# thresholds (default warning=0.4, alert=0.7):
#   score >= alert   -> ALERT
#   score >= warning -> WARNING
#   else             -> NORMAL
# Always returns status=SUCCESS; ALERT additionally seeds an interim narrative.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.


from framework.schemas.agent_status import AgentStatus

from src.nodes.anomaly_detection_node import AnomalyDetectionNode


def _run(score, settings=None):
    state = {"anomaly_score": score}
    if settings is not None:
        state["detection_settings"] = settings
    return AnomalyDetectionNode().execute(state)


class TestAnomalyClassification:
    def test_normal_below_warning(self):
        result = _run(0.1)
        assert result["anomaly_flag"] == "NORMAL"
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_warning_band(self):
        result = _run(0.5)
        assert result["anomaly_flag"] == "WARNING"
        assert result["status"] == AgentStatus.SUCCESS.value
        # WARNING does not seed the interim narrative (only ALERT does).
        assert "anomaly_narrative" not in result

    def test_alert_band(self):
        result = _run(0.9)
        assert result["anomaly_flag"] == "ALERT"
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["anomaly_narrative"]
        assert "ALERT" in result["anomaly_narrative"]


class TestAnomalyBoundary:
    def test_exactly_warning_threshold_is_warning(self):
        # >= warning -> WARNING at exactly 0.4.
        assert _run(0.4)["anomaly_flag"] == "WARNING"

    def test_exactly_alert_threshold_is_alert(self):
        # >= alert -> ALERT at exactly 0.7.
        assert _run(0.7)["anomaly_flag"] == "ALERT"

    def test_just_below_warning_is_normal(self):
        assert _run(0.399)["anomaly_flag"] == "NORMAL"

    def test_declared_thresholds_are_respected(self):
        # With warning=0.2, a 0.3 score becomes WARNING (default would be NORMAL).
        # The settings arrive on state because a node's contract is
        # execute(state) -> dict with no config argument.
        result = _run(0.3, settings={"warning_threshold": 0.2, "alert_threshold": 0.9})
        assert result["anomaly_flag"] == "WARNING"

    def test_none_score_defaults_to_normal(self):
        # A missing / None score must not crash; it floors to 0.0 -> NORMAL.
        result = AnomalyDetectionNode().execute({})
        assert result["anomaly_flag"] == "NORMAL"
        assert result["status"] == AgentStatus.SUCCESS.value
