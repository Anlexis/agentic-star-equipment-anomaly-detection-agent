# MFG-C2-010 — Unit Tests: BaselineLoadNode (inner domain node 2)
#
# Retrieves the normal operating profile (per-sensor mean/stddev/ucl/lcl) for
# the target equipment. Prefers an on-disk baseline file, falls back to the
# built-in profiles. Missing equipment_id or no baseline -> ERROR.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.


from framework.schemas.agent_status import AgentStatus

from src.nodes.baseline_load_node import BaselineLoadNode
from src.schemas.state import from_json


class TestBaselineLoadKnownEquipment:
    def test_builtin_profile_loaded(self):
        result = BaselineLoadNode().execute({"equipment_id": "CNC-LATHE-014"})
        assert "status" not in result or result.get("status") != AgentStatus.ERROR.value
        profile = from_json(result["baseline_profile"])
        assert "vibration_mm_s" in profile
        assert profile["vibration_mm_s"]["ucl"] == 4.0
        assert profile["vibration_mm_s"]["lcl"] == 0.0

    def test_profile_stored_as_json_string(self):
        # baseline_profile is a JSON STRING, not a bare dict.
        result = BaselineLoadNode().execute({"equipment_id": "CNC-LATHE-014"})
        assert isinstance(result["baseline_profile"], str)


class TestBaselineLoadFailures:
    def test_unknown_equipment_is_error(self):
        result = BaselineLoadNode().execute({"equipment_id": "UNKNOWN-MACHINE-999"})
        assert result["status"] == AgentStatus.SUCCESS.value
        # Completes carrying the reason, so the caller can correct the value and send the request again.
        assert result.get("error_code")
        assert any("baseline" in e.lower() for e in result["error_log"])

    def test_missing_equipment_id_is_error(self):
        result = BaselineLoadNode().execute({})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("equipment_id" in e for e in result["error_log"])

    def test_missing_baseline_file_path_falls_back_to_builtin(self):
        # Pointing at a directory with no matching file must NOT error for a
        # known equipment — the built-in profile is the fallback.
        result = BaselineLoadNode().execute(
            {
                "equipment_id": "CNC-LATHE-014",
                "detection_settings": {"baseline_path": "/nonexistent/baseline/dir"},
            }
        )
        assert result.get("status") != AgentStatus.ERROR.value
        profile = from_json(result["baseline_profile"])
        assert "spindle_temp_c" in profile
