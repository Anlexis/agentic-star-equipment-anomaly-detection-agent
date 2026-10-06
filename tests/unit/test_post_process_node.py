# MFG-C2-010 — Unit Tests: PostProcessNode (outer post_process slot)
#
# Reads state["result"] (the formatted anomaly result mapped from the inner
# graph), runs the output boundary over it, and surfaces formatted_output.
#   - clean report            -> SUCCESS, formatted_output == result (unchanged)
#   - empty / missing report  -> SUCCESS, nothing surfaced (non-fatal)
#   - a report it cannot vouch for -> ERROR, every output-bearing field cleared
#
# The exhaustive pattern battery and the clearing contract live in
# tests/proof_of_boundary/test_output_boundary.py; here we cover the node's
# three top-level return paths.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import json


from framework.schemas.agent_status import AgentStatus

from src.nodes.post_process_node import PostProcessNode


def _alert_result():
    # The shape the inner graph emits for an ALERT (no credential strings).
    return json.dumps(
        {
            "equipment_id": "CNC-LATHE-014",
            "anomaly_flag": "ALERT",
            "anomaly_score": 0.92,
            "probable_cause": "bearing_wear",
            "recommended_action": "STOP_EQUIPMENT",
            "narrative": "ALERT: anomaly score 0.920. Probable cause: bearing_wear.",
        }
    )


class TestPostProcessCleanPaths:
    def test_clean_result_passes_through(self):
        result = PostProcessNode().execute({"result": _alert_result()})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == _alert_result()

    def test_alert_payload_surfaced_intact(self):
        # The ALERT classification + action survive the gate untouched.
        result = PostProcessNode().execute({"result": _alert_result()})
        surfaced = json.loads(result["formatted_output"])
        assert surfaced["anomaly_flag"] == "ALERT"
        assert surfaced["recommended_action"] == "STOP_EQUIPMENT"

    def test_empty_result_is_non_fatal(self):
        result = PostProcessNode().execute({})
        assert result["status"] == AgentStatus.SUCCESS.value
        # Deliberately not asserting a specific falsy value: `formatted_output`
        # being falsy is what re-opens the fallback to `result`, so the property
        # that matters is that nothing was withheld and nothing was surfaced.
        assert not result["formatted_output"]
        assert "result" not in result

    def test_whitespace_result_is_non_fatal(self):
        result = PostProcessNode().execute({"result": "   "})
        assert result["status"] == AgentStatus.SUCCESS.value


class TestPostProcessWithholding:
    def test_credential_in_result_is_withheld(self):
        # A leaked credential assignment trips the boundary -> ERROR + clearing.
        tainted = _alert_result() + "\ninternal: password=supersecretvalue123"
        result = PostProcessNode().execute({"result": tainted})
        assert result["status"] == AgentStatus.ERROR.value
        # Presence AND emptiness: LangGraph merges partial deltas, so an omitted
        # key leaves the OLD value in state — `not result.get("result")` would
        # pass on a node that cleared nothing at all.
        assert "result" in result and not result["result"]
        assert result["formatted_output"], "a falsy notice re-opens the fallback"
        assert "supersecretvalue123" not in json.dumps(result)
        assert result["error_log"]
