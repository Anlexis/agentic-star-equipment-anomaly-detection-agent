# MFG-C2-010 — Unit Tests: PreProcessNode (outer pre_process slot)
#
# PreProcessNode is the caller boundary. It rejects empty / non-string input,
# applies the domain contract (payload size + equipment_id format), and on a
# clean request writes validated_input / equipment_id / analysis_window. Raw
# sensor arrays are never persisted.
#
# Direct execute({...}) calls bypass the BaseNode.__call__ trust gate (that gate
# fires only during a compiled graph run), so these node-level tests drive
# execute() directly and assert what the node itself guarantees.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import json


from framework.schemas.agent_status import AgentStatus

from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import from_json


def _payload(equipment_id="CNC-LATHE-014", **overrides):
    body = {
        "equipment_id": equipment_id,
        "analysis_window": {
            "start_time": "2025-01-01T00:00:00Z",
            "end_time": "2025-01-01T01:00:00Z",
            "window_size_minutes": 60,
        },
        "readings": [{"sensor_id": "vibration_mm_s", "value": 2.1, "unit": "mm/s"}],
    }
    body.update(overrides)
    return json.dumps(body)


class TestPreProcessSuccess:
    def test_valid_payload_returns_success(self):
        result = PreProcessNode().execute({"user_input": _payload()})
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_validated_input_and_equipment_id_set(self):
        result = PreProcessNode().execute({"user_input": _payload()})
        assert result["equipment_id"] == "CNC-LATHE-014"
        assert isinstance(result["validated_input"], str)
        # validated_input must be the (stripped) request JSON, parseable back.
        assert json.loads(result["validated_input"])["equipment_id"] == "CNC-LATHE-014"

    def test_analysis_window_stored_as_json_string(self):
        # Structured field persisted as a JSON STRING, not a bare dict.
        result = PreProcessNode().execute({"user_input": _payload()})
        assert isinstance(result["analysis_window"], str)
        window = from_json(result["analysis_window"])
        assert window["window_size_minutes"] == 60

    def test_enriched_context_carries_channel(self):
        result = PreProcessNode().execute({"user_input": _payload(), "input_context": {"channel": "scada"}})
        assert result["enriched_context"]["channel"] == "scada"
        assert result["enriched_context"]["source"] == ("ManufacturingEquipmentAnomalyDetectionAgent")


class TestPreProcessRejection:
    def test_empty_input_is_error(self):
        result = PreProcessNode().execute({"user_input": ""})
        assert result["status"] == AgentStatus.SUCCESS.value
        # Completes carrying the reason, so the caller can correct the value and send the request again.
        assert result.get("error_code")
        assert result["error_log"]

    def test_whitespace_only_is_error(self):
        result = PreProcessNode().execute({"user_input": "   \n\t "})
        assert result["status"] == AgentStatus.SUCCESS.value
        # Completes carrying the reason, so the caller can correct the value and send the request again.
        assert result.get("error_code")

    def test_missing_user_input_is_error(self):
        result = PreProcessNode().execute({})
        assert result["status"] == AgentStatus.SUCCESS.value
        # Completes carrying the reason, so the caller can correct the value and send the request again.
        assert result.get("error_code")

    def test_non_string_input_is_error(self):
        result = PreProcessNode().execute({"user_input": {"malicious": "dict"}})
        assert result["status"] == AgentStatus.SUCCESS.value
        # Completes carrying the reason, so the caller can correct the value and send the request again.
        assert result.get("error_code")

    def test_non_json_payload_is_error(self):
        # The request payload must be a JSON object.
        result = PreProcessNode().execute({"user_input": "just some free text"})
        assert result["status"] == AgentStatus.SUCCESS.value
        # Completes carrying the reason, so the caller can correct the value and send the request again.
        assert result.get("error_code")
        assert any("not a JSON object" in e for e in result["error_log"])

    def test_missing_equipment_id_is_error(self):
        result = PreProcessNode().execute({"user_input": json.dumps({"readings": []})})
        assert result["status"] == AgentStatus.SUCCESS.value
        # Completes carrying the reason, so the caller can correct the value and send the request again.
        assert result.get("error_code")
        assert any("equipment_id" in e for e in result["error_log"])

    def test_invalid_equipment_id_format_is_error(self):
        # Spaces / slashes are outside the accepted ^[A-Za-z0-9._-]{2,64}$ set.
        result = PreProcessNode().execute({"user_input": _payload(equipment_id="bad id/with spaces")})
        assert result["status"] == AgentStatus.SUCCESS.value
        # Completes carrying the reason, so the caller can correct the value and send the request again.
        assert result.get("error_code")
        assert any("equipment_id" in e for e in result["error_log"])

    def test_oversized_payload_is_error(self):
        # Size guard: a payload over the 200k-char limit is rejected at the
        # gate (not parsed). Pad a valid request body past the limit.
        big = "x" * 200001
        result = PreProcessNode().execute({"user_input": _payload(note=big)})
        assert result["status"] == AgentStatus.SUCCESS.value
        # Completes carrying the reason, so the caller can correct the value and send the request again.
        assert result.get("error_code")
        assert any("size limit" in e for e in result["error_log"])
