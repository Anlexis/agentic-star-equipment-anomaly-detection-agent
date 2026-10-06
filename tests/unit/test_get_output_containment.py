# MFG-C2-010 — Unit Tests: the surfaced-output resolution in MfgC2010Agent
#
# The inherited resolution is `formatted_output or result`, with no status
# check, so a run that never reached the output boundary can still surface the
# report the inner workflow assembled.
#
# REACHABILITY, stated plainly rather than implied by the tests below: with the
# shipped pipeline this override does not fire on any path a caller can drive.
# `result` is written only by merge_output, which the framework skips entirely
# when the inner workflow errors (error_strategy "propagate" raises first), so a
# failed run leaves `result` unset. The one case the override exists for is the
# case the output boundary structurally cannot cover — the framework raising
# inside post_process, which discards that node's whole delta including its
# clearing. The boundary uses the framework's own detector precisely so that
# cannot happen today, which is also why the state below is built by hand: the
# pipeline cannot currently produce it. These tests prove the override's logic,
# not that a caller can reach it. The end-to-end consequences that ARE reachable
# are in tests/proof_of_boundary/test_invoke_e2e.py.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import json

from framework.schemas.agent_status import AgentStatus

from src.graph.graph import MfgC2010Agent

_REPORT = json.dumps(
    {
        "equipment_id": "CNC-LATHE-014",
        "anomaly_flag": "NORMAL",
        "anomaly_score": 0.0,
        "probable_cause": "none",
        "recommended_action": "CONTINUE",
        "narrative": "",
    }
)


def _resolve(state):
    return MfgC2010Agent().get_output(state)["output"]


class TestNonSuccessNeverSurfacesAnUngatedReport:
    def test_a_discarded_boundary_delta_surfaces_nothing(self):
        # The shape the node wrapper produces when the framework raises inside
        # post_process: the ERROR partial clears nothing, so `result` is still
        # in state and `formatted_output` was never written.
        assert _resolve({"status": AgentStatus.ERROR.value, "result": _REPORT}) is None

    def test_the_inherited_resolution_would_have_surfaced_it(self):
        # Verifies the claim above rather than asserting it: the same state
        # through the inherited implementation.
        state = {"status": AgentStatus.ERROR.value, "result": _REPORT}
        inherited = super(MfgC2010Agent, MfgC2010Agent()).get_output(state)
        assert inherited["output"] == _REPORT

    def test_the_withheld_notice_still_reaches_the_caller(self):
        # A refusal the boundary DID write is not suppressed — the caller needs
        # to know the report was withheld rather than getting silence.
        state = {
            "status": AgentStatus.ERROR.value,
            "result": None,
            "formatted_output": "[Anomaly report withheld: ...]",
        }
        assert _resolve(state) == "[Anomaly report withheld: ...]"

    def test_an_empty_notice_does_not_re_open_the_fallback(self):
        # A falsy formatted_output is exactly what activates the inherited
        # fallback; on a non-success run it must resolve to nothing instead.
        assert _resolve({"status": AgentStatus.ERROR.value, "result": _REPORT, "formatted_output": ""}) is None


class TestSuccessIsUnaffected:
    def test_a_gated_report_is_surfaced(self):
        state = {"status": AgentStatus.SUCCESS.value, "formatted_output": _REPORT}
        assert _resolve(state) == _REPORT

    def test_the_other_envelope_fields_are_untouched(self):
        state = {
            "status": AgentStatus.SUCCESS.value,
            "formatted_output": _REPORT,
            "trace_id": "t-1",
            "correlation_id": "c-1",
            "node_history": ["PostProcessNode"],
        }
        output = MfgC2010Agent().get_output(state)
        assert output["trace_id"] == "t-1"
        assert output["correlation_id"] == "c-1"
        assert output["node_history"] == ["PostProcessNode"]
