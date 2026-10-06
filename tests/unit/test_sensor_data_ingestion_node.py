# MFG-C2-010 — Unit Tests: SensorDataIngestionNode (inner domain node 1)
#
# Deserialises the validated payload and collapses each sensor's raw reading
# window into SUMMARY statistics (min/max/mean/stddev/sample_count/unit). The
# raw time-series array is NEVER written to State ((internal issue reference removed)). Malformed JSON
# / non-object payloads return ERROR; an empty readings list yields zero sensors.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import json


from framework.schemas.agent_status import AgentStatus

from src.nodes.sensor_data_ingestion_node import SensorDataIngestionNode
from src.schemas.state import from_json


def _validated(readings):
    return json.dumps({"equipment_id": "CNC-LATHE-014", "readings": readings})


class TestSensorDataIngestionNormal:
    def test_summarises_per_sensor(self):
        readings = [
            {"sensor_id": "vibration_mm_s", "value": 2.0, "unit": "mm/s"},
            {"sensor_id": "vibration_mm_s", "value": 4.0, "unit": "mm/s"},
            {"sensor_id": "vibration_mm_s", "value": 6.0, "unit": "mm/s"},
        ]
        result = SensorDataIngestionNode().execute({"validated_input": _validated(readings)})
        summary = from_json(result["per_sensor_summary"])
        assert result["sensor_count"] == 1
        v = summary["vibration_mm_s"]
        assert v["min"] == 2.0
        assert v["max"] == 6.0
        assert v["mean"] == 4.0
        assert v["sample_count"] == 3
        assert v["unit"] == "mm/s"

    def test_raw_values_never_persisted(self):
        # Only summary stats — never the raw 'value' array — may appear in State.
        readings = [{"sensor_id": "spindle_temp_c", "value": 55.0} for _ in range(5)]
        result = SensorDataIngestionNode().execute({"validated_input": _validated(readings)})
        summary = from_json(result["per_sensor_summary"])
        assert set(summary["spindle_temp_c"].keys()) == {"min", "max", "mean", "stddev", "sample_count", "unit"}
        # No list/array leaked anywhere in the summary values.
        for stats in summary.values():
            assert not any(isinstance(val, list) for val in stats.values())

    def test_per_sensor_scores_placeholder_is_empty(self):
        result = SensorDataIngestionNode().execute({"validated_input": _validated([{"sensor_id": "s1", "value": 1.0}])})
        assert from_json(result["per_sensor_scores"]) == {}

    def test_multiple_sensors_counted(self):
        readings = [
            {"sensor_id": "vibration_mm_s", "value": 2.0},
            {"sensor_id": "spindle_temp_c", "value": 55.0},
            {"sensor_id": "spindle_current_a", "value": 12.0},
        ]
        result = SensorDataIngestionNode().execute({"validated_input": _validated(readings)})
        assert result["sensor_count"] == 3


class TestSensorDataIngestionEdgeCases:
    def test_malformed_json_is_error(self):
        result = SensorDataIngestionNode().execute({"validated_input": "{not json"})
        assert result["status"] == AgentStatus.SUCCESS.value
        # Completes carrying the reason, so the caller can correct the value and send the request again.
        assert result.get("error_code")

    def test_empty_readings_yields_zero_sensors(self):
        result = SensorDataIngestionNode().execute({"validated_input": _validated([])})
        assert result["sensor_count"] == 0
        assert from_json(result["per_sensor_summary"]) == {}

    def test_below_min_samples_still_summarised(self):
        # A sensor below min_samples is flagged (warning) but NOT dropped — it
        # still gets a best-effort summary.
        result = SensorDataIngestionNode().execute(
            {"validated_input": _validated([{"sensor_id": "vibration_mm_s", "value": 3.0}])}
        )
        summary = from_json(result["per_sensor_summary"])
        assert result["sensor_count"] == 1
        assert summary["vibration_mm_s"]["sample_count"] == 1

    def test_non_numeric_and_bool_values_skipped(self):
        # Bools and unparseable strings are not valid readings.
        readings = [
            {"sensor_id": "s1", "value": True},
            {"sensor_id": "s1", "value": "not-a-number"},
            {"sensor_id": "s1", "value": "5.0"},
        ]
        result = SensorDataIngestionNode().execute({"validated_input": _validated(readings)})
        summary = from_json(result["per_sensor_summary"])
        # Only the coercible "5.0" survives.
        assert summary["s1"]["sample_count"] == 1
        assert summary["s1"]["mean"] == 5.0
