# PB — Output Boundary: PostProcessNode withholds a report it cannot vouch for
#
# The surfaced value is resolved as `formatted_output or result`, with no status
# check. Three properties follow, and every assertion here is written against
# them rather than against a message:
#
#   1. A FALSY formatted_output re-opens the fallback, so "" or an absent key is
#      not withholding — the withheld notice must be non-empty.
#   2. LangGraph merges partial deltas, so a key the node omits keeps its OLD
#      value in state. `assert not result.get(field)` therefore passes on a gate
#      that clears nothing. Every clearing assertion below checks PRESENCE in
#      the returned delta AND emptiness of the value.
#   3. The framework re-scans every value this node returns and RAISES on a
#      credential it recognises — and the node wrapper then discards the whole
#      delta, clearing included. So the boundary uses the framework's own
#      detector: a narrower local set is a containment bypass, not a gap.
#
# Drives PostProcessNode.execute() directly — the post_process slot IS the
# boundary. The end-to-end consequences are in test_invoke_e2e.py.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.security.credential_detector import detect_credentials

from src.nodes.post_process_node import (
    _INERT_DOMAIN_FIELDS,
    _OUTPUT_BEARING_FIELDS,
    PostProcessNode,
)

# Simulated secrets / endpoints — NOT real credentials.
# Shapes the FRAMEWORK models. The boundary must recognise every one of them,
# because a value it misses makes the framework raise inside this node.
_FRAMEWORK_KNOWN = [
    "sk-abcdefghij0123456789ABCDEF",
    "sk_live_" + "abcdefghijklmnop0123",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    "AKIAIOSFODNN7EXAMPLE",
    "Bearer abcdefghijklmnop0123456789",
    "postgresql://svc:example-password@plc-historian.invalid:5432/readings",
]

# Equipment shapes the framework does NOT model. The domain patterns are the
# only layer that recognises these.
_DOMAIN_ONLY = [
    "opc.tcp://192.168.10.5:4840/cnc-lathe-014",
    "modbus://10.0.0.7:502/holding",
    "password=super_secret_password_abc123",
    "connection_string=Server=plc01;Pwd=hunter2hunter2",
]


def _clean_report(**overrides):
    base = {
        "equipment_id": "CNC-LATHE-014",
        "anomaly_flag": "ALERT",
        "anomaly_score": 0.92,
        "probable_cause": "bearing_wear",
        "recommended_action": "STOP_EQUIPMENT",
        "narrative": "ALERT: anomaly score 0.920. Probable cause: bearing_wear.",
    }
    base.update(overrides)
    return json.dumps(base)


def _state(report, **extra):
    state = {
        "result": report,
        "anomaly_narrative": "ALERT: anomaly score 0.920. Probable cause: bearing_wear.",
        "probable_cause": "bearing_wear",
    }
    state.update(extra)
    return state


def _assert_withheld(delta, secret=None):
    """The delta must WITHHOLD: error status, non-empty notice, fields cleared."""
    assert delta.get("status") == AgentStatus.ERROR.value, delta
    notice = delta.get("formatted_output")
    assert notice, "a falsy notice re-opens the fallback to the un-gated report"
    assert "withheld" in notice.lower()
    for field in _OUTPUT_BEARING_FIELDS:
        assert field in delta, (
            f"{field!r} is absent from the delta — LangGraph keeps the OLD value, "
            "so omitting a key is not clearing it"
        )
        assert not delta[field], f"{field!r} was returned with content: {delta[field]!r}"
    if secret is not None:
        assert secret not in json.dumps(delta)


class TestFrameworkRecognisedShapes:
    """The boundary's set is a SUPERSET of the framework's, by construction."""

    @pytest.mark.parametrize("secret", _FRAMEWORK_KNOWN)
    def test_the_fixture_is_one_the_framework_recognises(self, secret):
        assert detect_credentials(secret), "fixture no longer matches the framework detector"

    @pytest.mark.parametrize("secret", _FRAMEWORK_KNOWN)
    def test_it_is_withheld_here_before_the_framework_can_raise(self, secret):
        delta = PostProcessNode().execute(_state(_clean_report() + f"\nleaked: {secret}"))
        _assert_withheld(delta, secret)

    @pytest.mark.parametrize("secret", _FRAMEWORK_KNOWN)
    def test_the_delta_itself_survives_the_framework_scan(self, secret):
        # The proof that the boundary is a superset: run the framework's own
        # scan over what this node returns. A finding here means the framework
        # would raise, discarding the clearing above.
        delta = PostProcessNode().execute(_state(_clean_report() + f"\nleaked: {secret}"))
        for key, value in delta.items():
            assert (
                not detect_credentials(value) if isinstance(value, str) else True
            ), f"delta[{key!r}] still carries a credential the framework would raise on"


class TestDomainShapes:
    """Control-protocol endpoints and credential assignments are a topology leak
    the framework does not model."""

    @pytest.mark.parametrize("secret", _DOMAIN_ONLY)
    def test_the_fixture_is_invisible_to_the_framework(self, secret):
        assert not detect_credentials(secret), "fixture is no longer domain-only"

    @pytest.mark.parametrize("secret", _DOMAIN_ONLY)
    def test_it_is_withheld_by_the_domain_patterns(self, secret):
        delta = PostProcessNode().execute(_state(_clean_report() + f"\nendpoint: {secret}"))
        _assert_withheld(delta, secret)


class TestReportSchema:
    """The report is an aggregate document by contract."""

    def test_an_undeclared_field_is_withheld(self):
        # The shape a renderer change produces: per-sensor detail added to the
        # document without the boundary being told about it.
        drifted = _clean_report(per_sensor_readings=[9.0, 9.5, 10.0])
        _assert_withheld(PostProcessNode().execute(_state(drifted)))

    def test_a_missing_declared_field_is_withheld(self):
        partial = json.dumps({"equipment_id": "CNC-LATHE-014"})
        _assert_withheld(PostProcessNode().execute(_state(partial)))

    def test_a_non_json_report_is_withheld(self):
        _assert_withheld(PostProcessNode().execute(_state("ALERT on CNC-LATHE-014")))

    def test_a_non_string_report_is_withheld(self):
        # Skipping this case would leave the object in state, where the surfaced
        # value falls back to it.
        _assert_withheld(PostProcessNode().execute(_state({"equipment_id": "x"})))

    def test_an_undeclared_field_name_is_named_but_a_hostile_one_is_masked(self):
        drifted = _clean_report(**{"raw_readings": 1})
        delta = PostProcessNode().execute(_state(drifted))
        assert "raw_readings" in delta["error_log"][0]
        hostile = json.loads(_clean_report())
        hostile["endpoint host 10.0.0.1 port 4840"] = 1
        delta = PostProcessNode().execute(_state(json.dumps(hostile)))
        assert "<masked>" in delta["error_log"][0]
        assert "10.0.0.1" not in json.dumps(delta)


class TestViolationMessagesNameLocationsOnly:
    def test_the_matched_value_is_never_quoted(self):
        secret = "AKIAIOSFODNN7EXAMPLE"
        delta = PostProcessNode().execute(_state(_clean_report() + f"\nleaked: {secret}"))
        assert secret not in json.dumps(delta)
        # Nor the pattern that matched: which pattern fired is itself a fact
        # about the document that was withheld.
        assert "aws_key" not in json.dumps(delta)


class TestCleanPath:
    def test_a_clean_report_passes_through_unchanged(self):
        clean = _clean_report()
        delta = PostProcessNode().execute(_state(clean))
        assert delta["status"] == AgentStatus.SUCCESS.value, delta.get("error_log")
        assert delta["formatted_output"] == clean

    def test_a_clean_report_is_not_cleared(self):
        # The control that stops a refuse-everything boundary from passing.
        delta = PostProcessNode().execute(_state(_clean_report()))
        assert "result" not in delta, "a clean report must not be cleared"

    def test_an_absent_report_is_not_a_failure(self):
        delta = PostProcessNode().execute({})
        assert delta["status"] == AgentStatus.SUCCESS.value
        assert not delta["formatted_output"]

    def test_a_whitespace_report_is_not_a_failure(self):
        delta = PostProcessNode().execute({"result": "   "})
        assert delta["status"] == AgentStatus.SUCCESS.value


class TestOutputBearingInventory:
    """A future field cannot quietly become output-bearing without being cleared.

    The boundary clears an explicit list. That list is only trustworthy if it is
    checked against the fields that actually exist, so this pins the two
    inventories against the renderer's contract and the State annotations.
    """

    def test_every_rendered_field_is_accounted_for(self):
        from src.graph.domain_workflow_graph import REPORT_FIELDS

        # `narrative` is rendered from anomaly_narrative, which IS cleared.
        accounted = set(_OUTPUT_BEARING_FIELDS) | set(_INERT_DOMAIN_FIELDS) | {"narrative"}
        assert (
            set(REPORT_FIELDS) <= accounted
        ), f"rendered field(s) in neither inventory: {sorted(set(REPORT_FIELDS) - accounted)}"

    def test_the_two_inventories_do_not_overlap(self):
        assert not set(_OUTPUT_BEARING_FIELDS) & set(_INERT_DOMAIN_FIELDS)

    def test_every_domain_state_field_is_classified(self):
        from framework.schemas.agent_state import AgentState

        from src.schemas.state import State

        domain_fields = set(State.__annotations__) - set(AgentState.__annotations__)
        # Fields that are inputs or internal working state, never surfaced.
        internal = {
            "validated_input",
            "analysis_window",
            "detection_settings",
            "per_sensor_summary",
            "per_sensor_scores",
            "sensor_count",
            "baseline_profile",
            "trace_id",
            "correlation_id",
            # The reason a run completed without carrying out the request. It is
            # a closed-set marker, never caller content and never a product, so
            # it is not output-bearing. It is deliberately NOT cleared either:
            # the boundary clears the report, and clearing the reason too would
            # leave the caller holding an empty body with nothing to correct.
            "error_code",
        }
        unclassified = domain_fields - set(_OUTPUT_BEARING_FIELDS) - set(_INERT_DOMAIN_FIELDS) - internal
        assert not unclassified, (
            f"new State field(s) with no output classification: {sorted(unclassified)}. "
            "Decide whether each is output-bearing (and must be cleared) or inert."
        )
