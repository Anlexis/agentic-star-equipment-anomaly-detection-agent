# PB — Domain Boundary: raw sensor time-series is never persisted to State
#
# MFG-C2-010 sensor-data safety invariant: raw sensor reading
# windows are collapsed to per-sensor SUMMARY statistics by
# SensorDataIngestionNode BEFORE anything reaches State. The raw arrays must
# never appear in any State field, and equipment credentials / connection
# strings are never persisted. State carries only msgpack-safe scalars + JSON
# strings.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import json


from src.nodes.sensor_data_ingestion_node import SensorDataIngestionNode
from src.schemas.state import from_json


# A long, distinctive raw window — if any raw value leaks, it is recognisable.
_RAW_VALUES = [101.111, 202.222, 303.333, 404.444, 505.555, 606.666]


def _validated():
    return json.dumps(
        {
            "equipment_id": "CNC-LATHE-014",
            "connection_string": "Server=plc01;User=admin;Pwd=hunter2",
            "readings": [{"sensor_id": "vibration_mm_s", "value": v, "unit": "mm/s"} for v in _RAW_VALUES],
        }
    )


class TestRawSensorDataNotPersisted:
    def test_summary_only_no_raw_array(self):
        result = SensorDataIngestionNode().execute({"validated_input": _validated()})
        summary = from_json(result["per_sensor_summary"])
        stats = summary["vibration_mm_s"]
        # Only summary keys — no raw 'value'/'readings' array.
        assert set(stats.keys()) == {"min", "max", "mean", "stddev", "sample_count", "unit"}
        for val in stats.values():
            assert not isinstance(val, list), "raw array leaked into a summary field"

    def test_individual_raw_values_absent_from_state(self):
        result = SensorDataIngestionNode().execute({"validated_input": _validated()})
        # Serialise the entire returned state delta and confirm no interior raw
        # reading survives verbatim. (min/max legitimately equal the extreme raw
        # values, so check the strictly-interior samples.)
        blob = json.dumps(result)
        for interior in (202.222, 303.333, 404.444, 505.555):
            assert str(interior) not in blob, f"interior raw reading {interior} leaked into State"

    def test_connection_string_not_persisted(self):
        # Equipment credentials in the payload must never reach a State field.
        result = SensorDataIngestionNode().execute({"validated_input": _validated()})
        blob = json.dumps(result)
        assert "hunter2" not in blob
        assert "connection_string" not in blob

    def test_sample_count_reflects_window_size(self):
        # The summary records HOW MANY readings there were without storing them.
        result = SensorDataIngestionNode().execute({"validated_input": _validated()})
        summary = from_json(result["per_sensor_summary"])
        assert summary["vibration_mm_s"]["sample_count"] == len(_RAW_VALUES)
