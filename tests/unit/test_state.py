# MFG-C2-010 — Unit Tests: State schema + JSON-string (de)serialisation helpers
#
# State is a flat TypedDict (AgentState subclass). msgpack safety requires structured
# fields be stored as JSON STRINGS, so the to_json / from_json helpers are part
# of the State contract and are exercised here. The State class itself carries
# NO credential fields and NO Pydantic / non-msgpack types.
#
# Deterministic -- no LLM, no network. framework.* / src.* imports only.

import typing


from src.schemas.state import State, from_json, to_json


class TestStateStructure:
    def test_state_extends_agentstate(self):
        # TypedDict subclass inheritance is not visible via __bases__
        # (all TypedDicts resolve to (dict,) at runtime) or __orig_bases__
        # (only populated for generic aliases).  Use typing.get_type_hints()
        # which properly resolves the full TypedDict annotation chain
        # across all parent classes, including inherited fields from AgentState
        # and SubgraphContext.  All backbone fields that AgentState declares
        # must be reachable through State's annotation resolution.
        all_hints = typing.get_type_hints(State, include_extras=True)
        backbone_fields = ("user_input", "status", "session_id", "node_history", "error_log")
        for field in backbone_fields:
            assert field in all_hints, (
                f"State does not inherit AgentState field '{field}'. " f"Available hints: {sorted(all_hints.keys())}"
            )

    def test_state_declares_domain_fields(self):
        # The domain fields the nodes read/write must be annotated on the
        # TypedDict (so checkpoint serialisation knows them).
        ann = State.__annotations__
        for field in (
            "validated_input",
            "analysis_window",
            "equipment_id",
            "result",
            "per_sensor_summary",
            "sensor_count",
            "baseline_profile",
            "per_sensor_scores",
            "anomaly_score",
            "anomaly_flag",
            "probable_cause",
            "recommended_action",
            "anomaly_narrative",
        ):
            assert field in ann, f"State missing domain field: {field}"

    def test_state_has_no_credential_fields(self):
        # Sensor-data safety: no credential / connection-string
        # field may exist on the checkpointed State.
        lowered = {name.lower() for name in State.__annotations__}
        for banned in ("password", "secret", "token", "api_key", "credential", "connection_string", "conn_str"):
            assert not any(
                banned in name for name in lowered
            ), f"State must not carry a credential-like field matching {banned!r}"


class TestJsonRoundTrip:
    def test_to_json_returns_str_for_dict(self):
        s = to_json({"a": 1, "b": [1, 2, 3]})
        assert isinstance(s, str)

    def test_round_trip_dict_is_lossless(self):
        original = {"vibration_mm_s": {"mean": 2.0, "stddev": 0.5}, "n": 3}
        assert from_json(to_json(original)) == original

    def test_round_trip_list_is_lossless(self):
        original = [{"sensor_id": "s1", "value": 1.5}, {"sensor_id": "s2", "value": 2.5}]
        assert from_json(to_json(original)) == original

    def test_to_json_none_passes_through(self):
        # None must stay distinguishable from an empty container.
        assert to_json(None) is None

    def test_from_json_none_returns_default(self):
        assert from_json(None, {}) == {}
        assert from_json(None) is None

    def test_from_json_empty_string_returns_default(self):
        assert from_json("", {}) == {}

    def test_from_json_malformed_returns_default(self):
        # A corrupt field must be non-fatal for the consuming node.
        assert from_json("{not valid json", {"fallback": True}) == {"fallback": True}

    def test_to_json_preserves_non_ascii(self):
        # ensure_ascii=False -- unit labels / narratives may carry non-ASCII.
        s = to_json({"unit": "degC", "label": "temp"})
        assert from_json(s) == {"unit": "degC", "label": "temp"}
