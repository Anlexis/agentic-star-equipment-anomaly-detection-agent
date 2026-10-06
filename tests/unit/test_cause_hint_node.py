# MFG-C2-010 — Unit Tests: CauseHintNode (inner domain node 5)
#
# Maps the top-scoring sensor to a probable mechanical cause using a substring
# rule table (e.g. "vibration" -> bearing_wear, "temp" -> coolant_temperature_drift).
# NORMAL equipment (or no scores) yields "none"; an unmatched sensor yields
# "undetermined".
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.


from src.nodes.cause_hint_node import _CAUSE_RULES, CauseHintNode
from src.schemas.state import to_json


def _run(anomaly_flag, scores):
    state = {"anomaly_flag": anomaly_flag, "per_sensor_scores": to_json(scores)}
    return CauseHintNode().execute(state)


class TestCauseHintKnownPattern:
    def test_vibration_maps_to_bearing_wear(self):
        result = _run("ALERT", {"vibration_mm_s": 0.9, "spindle_temp_c": 0.1})
        assert result["probable_cause"] == "bearing_wear"

    def test_temp_maps_to_coolant_drift(self):
        result = _run("WARNING", {"spindle_temp_c": 0.6, "vibration_mm_s": 0.0})
        assert result["probable_cause"] == "coolant_temperature_drift"

    def test_current_maps_to_load_spike(self):
        result = _run("WARNING", {"spindle_current_a": 0.5})
        assert result["probable_cause"] == "spindle_load_spike"


class TestCauseHintEdgeCases:
    def test_unknown_sensor_is_undetermined(self):
        # Top sensor matches no rule -> "undetermined".
        result = _run("ALERT", {"mystery_sensor": 0.95})
        assert result["probable_cause"] == "undetermined"

    def test_normal_flag_has_no_cause(self):
        # NORMAL equipment has no actionable cause even if scores exist.
        result = _run("NORMAL", {"vibration_mm_s": 0.9})
        assert result["probable_cause"] == "none"

    def test_no_scores_has_no_cause(self):
        result = _run("ALERT", {})
        assert result["probable_cause"] == "none"

    def test_zero_top_score_has_no_cause(self):
        # Anomalous flag but the top score is 0.0 -> nothing to attribute.
        result = _run("WARNING", {"vibration_mm_s": 0.0, "spindle_temp_c": 0.0})
        assert result["probable_cause"] == "none"

    def test_cause_vocabulary_is_closed(self):
        # The cause string renders into the report, so it comes only from the
        # reviewed rule table — no configured or caller-supplied value can put a
        # new string there.
        result = _run("ALERT", {"vibration_mm_s": 0.9})
        assert result["probable_cause"] in {cause for _, cause in _CAUSE_RULES} | {
            "none",
            "undetermined",
        }
