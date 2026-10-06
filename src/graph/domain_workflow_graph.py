"""AgentCore Platform v1.0"""

# MFG-C2-010 — DomainWorkflowGraph (inner BaseGraph)
#
# This is the INNER graph for the Cat 2 two-layer nested architecture.
# It encapsulates the full manufacturing-equipment anomaly-detection workflow:
#
#   START -> sensor_data_ingestion -> baseline_load -> statistical_analysis
#         -> anomaly_detection -> cause_hint -> action_recommendation -> END
#
# Called by AnomalyDetectionGraphNode.get_subgraph() (graph.py).
# get_output() shapes the sub_result dict consumed by merge_output() there.
#
# Rules enforced:
#   - Inherits BaseGraph (fully custom topology — no forced backbone)
#   - Implements all 7 BaseGraph ABC methods
#   - register_nodes() does NOT call super() (abstract in BaseGraph)
#   - register_nodes() instantiates every domain node with NO ctor args
#   - Does NOT register initialize / finalize (outer backbone concerns)
#   - get_output() designed together with AnomalyDetectionGraphNode.merge_output()
#   - No agenticstar imports

import json
from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.action_recommendation_node import ActionRecommendationNode
from src.nodes.anomaly_detection_node import AnomalyDetectionNode
from src.nodes.baseline_load_node import BaselineLoadNode
from src.nodes.cause_hint_node import CauseHintNode
from src.nodes.sensor_data_ingestion_node import SensorDataIngestionNode
from src.nodes.statistical_analysis_node import StatisticalAnalysisNode
from src.schemas.state import State


# The rendered report's key set. The output boundary checks the surfaced
# document against exactly this set, so a field added to the renderer without
# being added here is refused rather than shipped unreviewed.
REPORT_FIELDS = (
    "equipment_id",
    "anomaly_flag",
    "anomaly_score",
    "probable_cause",
    "recommended_action",
    "narrative",
)


def _build_formatted_output(state: AgentState) -> str:
    """Render the structured anomaly result the outer graph surfaces as `result`.

    A compact, deterministic JSON document the maintenance-team consumer (and
    the output boundary in PostProcessNode) reads.

    The report is an AGGREGATE document by contract: it carries the equipment
    identifier, the classified flag, the aggregate score and the derived action.
    Individual sensor readings and the per-sensor statistics computed from them
    are never rendered — they stay inside the graph.
    """
    payload = {
        "equipment_id": state.get("equipment_id", ""),
        "anomaly_flag": state.get("anomaly_flag", "NORMAL"),
        "anomaly_score": state.get("anomaly_score", 0.0),
        "probable_cause": state.get("probable_cause", "none"),
        "recommended_action": state.get("recommended_action", "CONTINUE"),
        "narrative": state.get("anomaly_narrative", ""),
    }
    return json.dumps(payload, ensure_ascii=False)


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for MFG-C2-010.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by AnomalyDetectionGraphNode.get_subgraph() in graph.py.

    Pipeline (linear):
        START
          -> sensor_data_ingestion (SensorDataIngestionNode) — summarize payload
          -> baseline_load         (BaselineLoadNode)         — load normal profile
          -> statistical_analysis  (StatisticalAnalysisNode)  — per-sensor scoring
          -> anomaly_detection     (AnomalyDetectionNode)     — classify flag
          -> cause_hint            (CauseHintNode)            — probable cause
          -> action_recommendation (ActionRecommendationNode) — action + narrative
          -> END

    All nodes are FunctionNode subclasses returning partial-dict state updates.
    initialize / finalize are outer backbone concerns — not registered here.
    """

    # -- Identity --------------------------------------------------------------

    @property
    def name(self) -> str:
        """Unique identifier for this inner graph."""
        return "mfg_c2_010_anomaly_workflow"

    @property
    def state_schema(self) -> type:
        """TypedDict subclass shared across inner and outer graph."""
        return State

    # -- Config validation -----------------------------------------------------

    def _validate_config(self) -> None:
        """Validate inner graph config before compilation.

        Every declared setting was already parsed and bounded by the outer
        graph before it was handed over (see AnomalyDetectionGraphNode.
        _parent_config), and an absent setting is non-fatal by design — each
        node falls back to its own documented default. There is therefore no
        additional constraint to enforce here and nothing to raise for.
        """
        return

    # -- Node registration -----------------------------------------------------

    def register_nodes(self) -> None:
        """Register all 6 domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.

        Every node is instantiated with NO constructor arguments — FunctionNode
        subclasses take no __init__, and a node's contract is
        execute(self, state) -> dict, so the declared settings reach a node
        through the seeded initial state (see _extra_initial_state). Every key
        registered here is referenced in add_edges().
        """
        self._nodes["sensor_data_ingestion"] = SensorDataIngestionNode()
        self._nodes["baseline_load"] = BaselineLoadNode()
        self._nodes["statistical_analysis"] = StatisticalAnalysisNode()
        self._nodes["anomaly_detection"] = AnomalyDetectionNode()
        self._nodes["cause_hint"] = CauseHintNode()
        self._nodes["action_recommendation"] = ActionRecommendationNode()

    # -- Edge wiring -----------------------------------------------------------

    def add_edges(self) -> None:
        """Wire the linear anomaly-detection domain topology.

        Each step passes its partial-dict output into the shared State.
        For this template the topology is intentionally linear — no conditional
        branching between domain nodes (cause_hint / action_recommendation are
        effectively no-ops for NORMAL). route() is implemented as required by the
        ABC but add_conditional_edges() is not used.
        """
        self._sg.add_edge(START, "sensor_data_ingestion")
        self._sg.add_edge("sensor_data_ingestion", "baseline_load")
        self._sg.add_edge("baseline_load", "statistical_analysis")
        self._sg.add_edge("statistical_analysis", "anomaly_detection")
        self._sg.add_edge("anomaly_detection", "cause_hint")
        self._sg.add_edge("cause_hint", "action_recommendation")
        self._sg.add_edge("action_recommendation", END)

    # -- Declared settings -----------------------------------------------------

    def _extra_initial_state(self) -> dict[str, Any]:
        """Seed the declared detection settings into the inner initial state.

        The framework builds a FRESH initial state for a subgraph invocation
        from the string returned by extract_input() plus the standard framework
        fields, and calls each node as execute(state) with no config argument.
        This hook is the only route by which a value declared in
        config/config.yaml reaches a domain node, so the settings the outer
        graph validated are placed on the state the nodes read.
        """
        return {"detection_settings": dict(self.config)}

    # -- Routing ---------------------------------------------------------------

    def route(self, state: AgentState) -> str:
        """Conditional routing — required by BaseGraph ABC.

        For this linear topology add_conditional_edges() is not used, so this
        method is never called at runtime. It is implemented to satisfy the ABC
        contract. Returns END on error so an unexpected call does not re-enter a
        processing node.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "action_recommendation"

    # -- Output shape ----------------------------------------------------------

    def get_output(self, state: AgentState) -> dict[str, Any]:
        """Shape the output dict returned to the outer graph as sub_result.

        This dict is received by AnomalyDetectionGraphNode.merge_output() in
        graph.py as the `sub_result` argument. Both methods are designed together
        to guarantee field-name consistency:

            Inner get_output()  emits: "output", "equipment_id", "anomaly_flag",
                                       "anomaly_score", "probable_cause",
                                       "recommended_action", "anomaly_narrative",
                                       "status", ...
            Outer merge_output() reads those keys back.

        "output" is the rendered structured result; merge_output() maps it to the
        outer state's "result" (consumed by the output boundary in PostProcessNode).
        """
        return {
            # the reason must leave the subgraph or the outer graph cannot report it
            "error_code": state.get("error_code"),
            "output": _build_formatted_output(state),
            "equipment_id": state.get("equipment_id", ""),
            "anomaly_flag": state.get("anomaly_flag", "NORMAL"),
            "anomaly_score": state.get("anomaly_score", 0.0),
            "probable_cause": state.get("probable_cause", "none"),
            "recommended_action": state.get("recommended_action", "CONTINUE"),
            "anomaly_narrative": state.get("anomaly_narrative", ""),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
