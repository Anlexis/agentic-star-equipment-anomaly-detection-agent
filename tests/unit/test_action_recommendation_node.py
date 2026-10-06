# MFG-C2-010 — Unit Tests: ActionRecommendationNode (inner domain node 6, last)
#
# Maps (anomaly_flag, probable_cause) to a recommended maintenance action and
# renders the maintenance-team narrative:
#   ALERT   + {bearing_wear, hydraulic_pressure_loss} -> STOP_EQUIPMENT
#   ALERT   + other                                   -> ESCALATE_MAINTENANCE
#   WARNING + {spindle_load_spike, coolant_temperature_drift,
#              hydraulic_pressure_loss}               -> REDUCE_LOAD
#   WARNING + other                                   -> INSPECT
#   NORMAL / unknown flag                             -> CONTINUE
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.


from src.nodes.action_recommendation_node import ActionRecommendationNode


def _run(anomaly_flag, probable_cause="none", anomaly_score=0.0):
    state = {
        "anomaly_flag": anomaly_flag,
        "probable_cause": probable_cause,
        "anomaly_score": anomaly_score,
    }
    return ActionRecommendationNode().execute(state)


class TestActionResolution:
    def test_normal_recommends_continue(self):
        result = _run("NORMAL", "none", 0.1)
        assert result["recommended_action"] == "CONTINUE"
        assert result["anomaly_narrative"]
        assert "normal" in result["anomaly_narrative"].lower()

    def test_warning_inspect_default(self):
        # A WARNING cause not in the reduce-load set -> INSPECT.
        result = _run("WARNING", "bearing_wear", 0.5)
        assert result["recommended_action"] == "INSPECT"

    def test_warning_reduce_load_for_thermal_cause(self):
        result = _run("WARNING", "coolant_temperature_drift", 0.5)
        assert result["recommended_action"] == "REDUCE_LOAD"

    def test_alert_stop_for_mechanical_failure_cause(self):
        result = _run("ALERT", "bearing_wear", 0.9)
        assert result["recommended_action"] == "STOP_EQUIPMENT"

    def test_alert_escalate_default(self):
        # An ALERT cause not in the stop set -> ESCALATE_MAINTENANCE.
        result = _run("ALERT", "spindle_load_spike", 0.9)
        assert result["recommended_action"] == "ESCALATE_MAINTENANCE"


class TestActionFallbackAndNarrative:
    def test_unknown_cause_at_alert_escalates(self):
        # Unknown / undetermined cause must still resolve to a safe ALERT action.
        result = _run("ALERT", "undetermined", 0.95)
        assert result["recommended_action"] == "ESCALATE_MAINTENANCE"

    def test_unknown_flag_falls_back_to_continue(self):
        # Any unrecognised flag -> safe default CONTINUE.
        result = _run("UNRECOGNISED_FLAG", "none", 0.0)
        assert result["recommended_action"] == "CONTINUE"

    def test_narrative_includes_flag_cause_and_action_for_alert(self):
        result = _run("ALERT", "bearing_wear", 0.9)
        narrative = result["anomaly_narrative"]
        assert "ALERT" in narrative
        assert "bearing_wear" in narrative
        assert "STOP_EQUIPMENT" in narrative
