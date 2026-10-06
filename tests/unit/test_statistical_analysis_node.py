# MFG-C2-010 — Unit Tests: StatisticalAnalysisNode (inner domain node 3)
#
# Computes a per-sensor anomaly score from the ingested summary statistics vs.
# the equipment baseline, then aggregates to a single anomaly_score in [0,1].
# Score per sensor = max(zscore_component, control_limit_violation_component).
# Aggregate = 0.7*worst + 0.3*mean. A sensor with no baseline scores 0.0.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import pytest

from src.nodes.statistical_analysis_node import StatisticalAnalysisNode
from src.schemas.state import from_json, to_json


def _summary(**sensors):
    """sensors: sensor_id -> {min,max,mean,stddev,sample_count,unit}."""
    return to_json(sensors)


def _baseline(**sensors):
    """sensors: sensor_id -> {mean,stddev,ucl,lcl}."""
    return to_json(sensors)


def _run(summary, baseline, settings=None):
    state = {"per_sensor_summary": summary, "baseline_profile": baseline}
    if settings is not None:
        state["detection_settings"] = settings
    return StatisticalAnalysisNode().execute(state)


class TestStatisticalAnalysisNormal:
    def test_on_baseline_scores_zero(self):
        # mean == base mean and within control limits -> score 0.0.
        summary = _summary(vib={"min": 2.0, "max": 2.0, "mean": 2.0, "stddev": 0.1})
        baseline = _baseline(vib={"mean": 2.0, "stddev": 0.5, "ucl": 4.0, "lcl": 0.0})
        result = _run(summary, baseline)
        scores = from_json(result["per_sensor_scores"])
        assert scores["vib"] == 0.0
        assert result["anomaly_score"] == 0.0

    def test_output_keys_are_scores_and_aggregate(self):
        summary = _summary(vib={"min": 2.0, "max": 2.0, "mean": 2.0, "stddev": 0.1})
        baseline = _baseline(vib={"mean": 2.0, "stddev": 0.5, "ucl": 4.0, "lcl": 0.0})
        result = _run(summary, baseline)
        assert set(result.keys()) == {"per_sensor_scores", "anomaly_score"}
        assert isinstance(result["per_sensor_scores"], str)
        assert isinstance(result["anomaly_score"], float)


class TestStatisticalAnalysisAnomaly:
    def test_single_sensor_control_limit_breach_scores_high(self):
        # max (9.0) > ucl (4.0) -> violation component 1.0 -> sensor score 1.0.
        summary = _summary(vib={"min": 2.0, "max": 9.0, "mean": 5.0, "stddev": 1.0})
        baseline = _baseline(vib={"mean": 2.0, "stddev": 0.5, "ucl": 4.0, "lcl": 0.0})
        result = _run(summary, baseline)
        scores = from_json(result["per_sensor_scores"])
        assert scores["vib"] == 1.0
        # Single sensor: aggregate = 0.7*1.0 + 0.3*1.0 = 1.0.
        assert result["anomaly_score"] == pytest.approx(1.0)

    def test_multi_sensor_worst_drives_aggregate(self):
        # One breaching sensor (score 1.0) + one clean (score 0.0):
        # aggregate = 0.7*1.0 + 0.3*0.5 = 0.85.
        summary = _summary(
            vib={"min": 2.0, "max": 9.0, "mean": 5.0, "stddev": 1.0},
            temp={"min": 50.0, "max": 60.0, "mean": 55.0, "stddev": 2.0},
        )
        baseline = _baseline(
            vib={"mean": 2.0, "stddev": 0.5, "ucl": 4.0, "lcl": 0.0},
            temp={"mean": 55.0, "stddev": 4.0, "ucl": 75.0, "lcl": 20.0},
        )
        result = _run(summary, baseline)
        scores = from_json(result["per_sensor_scores"])
        assert scores["vib"] == 1.0
        assert scores["temp"] == 0.0
        assert result["anomaly_score"] == pytest.approx(0.85)

    def test_sensor_without_baseline_scores_zero(self):
        # Missing baseline for the sensor -> cannot score -> treated as normal.
        summary = _summary(orphan={"min": 1.0, "max": 99.0, "mean": 50.0, "stddev": 1.0})
        baseline = _baseline(other={"mean": 1.0, "stddev": 1.0, "ucl": 2.0, "lcl": 0.0})
        result = _run(summary, baseline)
        scores = from_json(result["per_sensor_scores"])
        assert scores["orphan"] == 0.0
        assert result["anomaly_score"] == 0.0


class TestStatisticalAnalysisBoundary:
    def test_empty_summary_scores_zero(self):
        result = _run(to_json({}), to_json({}))
        assert from_json(result["per_sensor_scores"]) == {}
        assert result["anomaly_score"] == 0.0

    def test_zscore_component_saturates_at_one(self):
        # |mean - base_mean| / stddev = 8 / 1 = 8; /4.0 saturation -> clamps to 1.0,
        # and the score never exceeds 1.0.
        summary = _summary(s={"min": 9.0, "max": 9.5, "mean": 9.0, "stddev": 0.1})
        baseline = _baseline(s={"mean": 1.0, "stddev": 1.0, "ucl": 100.0, "lcl": -100.0})
        result = _run(summary, baseline)
        scores = from_json(result["per_sensor_scores"])
        assert scores["s"] == 1.0
        assert 0.0 <= result["anomaly_score"] <= 1.0
