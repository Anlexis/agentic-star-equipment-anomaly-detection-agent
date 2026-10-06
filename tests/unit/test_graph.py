# MFG-C2-010 — Unit Tests: outer graph (MfgC2010Agent / Graph) composition
#
# The outer graph inherits AgentBaseGraph directly (L1 Base). It overrides only
# register_nodes() to fill the pre_process / main / post_process slots; the
# backbone wiring (add_edges) belongs to the framework. The main slot is the
# AnomalyDetectionGraphNode wrapper whose merge_output() maps the inner graph's
# sub_result into the outer state.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import json


from framework.graph.agent_base_graph import AgentBaseGraph
from framework.schemas.agent_status import AgentStatus

from src.graph.graph import AnomalyDetectionGraphNode, Graph, MfgC2010Agent
from src.schemas.state import State


class TestOuterGraphIdentity:
    def test_back_compat_alias(self):
        assert Graph is MfgC2010Agent

    def test_inherits_l1_agent_base_graph(self):
        assert issubclass(MfgC2010Agent, AgentBaseGraph)

    def test_name_and_state_schema(self):
        agent = MfgC2010Agent()
        assert agent.name == "ManufacturingEquipmentAnomalyDetectionAgent"
        assert agent.state_schema is State


class TestOuterGraphComposition:
    def test_compile_fills_all_backbone_slots(self):
        agent = MfgC2010Agent()
        agent.compile()
        for slot in ("initialize", "pre_process", "main", "post_process", "finalize"):
            assert agent._nodes.get(slot) is not None, f"backbone slot not filled: {slot}"

    def test_main_slot_is_graph_node_wrapper(self):
        agent = MfgC2010Agent()
        agent.register_nodes()
        assert isinstance(agent._nodes["main"], AnomalyDetectionGraphNode)


class TestMergeOutputMapping:
    """AnomalyDetectionGraphNode.merge_output() is designed together with the
    inner get_output(): the inner 'output' must land in the outer 'result' (the
    field the output boundary in PostProcessNode reads)."""

    def test_output_maps_to_result(self):
        node = AnomalyDetectionGraphNode()
        sub_result = {
            "output": json.dumps({"anomaly_flag": "ALERT"}),
            "anomaly_flag": "ALERT",
            "anomaly_score": 0.9,
            "anomaly_narrative": "n",
            "probable_cause": "bearing_wear",
            "recommended_action": "STOP_EQUIPMENT",
            "status": AgentStatus.SUCCESS.value,
        }
        delta = node.merge_output({}, sub_result)
        assert delta["result"] == sub_result["output"]
        assert delta["anomaly_flag"] == "ALERT"
        assert delta["recommended_action"] == "STOP_EQUIPMENT"
        assert delta["status"] == AgentStatus.SUCCESS.value

    def test_extract_input_prefers_validated_input(self):
        node = AnomalyDetectionGraphNode()
        assert node.extract_input({"validated_input": "V", "user_input": "U"}) == "V"
        # Falls back to user_input when validated_input is absent.
        assert node.extract_input({"user_input": "U"}) == "U"

    def test_get_subgraph_returns_inner_graph(self):
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        node = AnomalyDetectionGraphNode()
        assert isinstance(node.get_subgraph(), DomainWorkflowGraph)
