"""AgentCore Platform v1.0"""

# MFG-C2-010 — Outer graph (AgentBaseGraph; Cat 2 two-layer nested architecture)
#
# Manufacturing Equipment Anomaly Detection Agent (Cat 2 domain workflow).
#
# Architecture (Cat 2):
#
#   Outer backbone (fixed — identical to Cat 1, do NOT override add_edges()):
#     START -> initialize -> pre_process -> main -> {route} -> post_process -> finalize -> END
#                                               (RETRY -> pre_process, max 3)
#
#   `main` slot is a GraphNode subclass (AnomalyDetectionGraphNode) that
#   delegates the full anomaly-detection domain workflow to DomainWorkflowGraph
#   (inner BaseGraph).
#
#   Domain complexity is fully encapsulated inside the inner graph. The outer
#   backbone is never modified.
#
# Directory layout:
#   src/graph/graph.py                 <- outer graph (this file)
#   src/graph/domain_workflow_graph.py <- inner graph (multi-step topology)
#
# Rules enforced:
#   - MfgC2010Agent inherits AgentBaseGraph directly (framework base class)
#   - super().register_nodes() called first (fills initialize + finalize)
#   - AnomalyDetectionGraphNode assigned to self._nodes["main"]
#   - merge_output() returns only changed keys
#   - add_edges() NOT overridden on the outer graph
#   - No platform-internal imports

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Optional, cast

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus

from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State
from src.services.service import finite_in_range

if TYPE_CHECKING:
    from src.graph.domain_workflow_graph import DomainWorkflowGraph

# Runtime-parameter file: src/graph/graph.py -> parents[2] is the repo root.
# config/agent.yaml holds the registration identity only (a flat manifest with no
# runtime block); every runtime parameter lives in config/config.yaml, which is
# the file the platform registry loads and passes as Graph(config=...).
_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

# Bounds for the declared detection settings. A configuration file is not caller
# data, but it is still an input: an out-of-range or non-finite threshold would
# silently reclassify every reading, so each value is parsed and bounded exactly
# like a caller field and an invalid entry falls back to the node default rather
# than propagating.
_THRESHOLD_BOUNDS = (0.0, 1.0)
_ZSCORE_SATURATION_BOUNDS = (0.1, 1000.0)
_MIN_SAMPLES_BOUNDS = (1.0, 100000.0)


def runtime_config() -> dict[str, Any]:
    """Read the runtime parameters from config/config.yaml.

    The standalone server (src/api/server.py) calls this so a directly deployed
    agent and a registry-loaded agent see identical configuration. Returns an
    empty dict — never raises — when the file is absent, unreadable, not valid
    YAML, or not a mapping; the graph then runs on its built-in defaults.
    """
    try:
        import yaml

        loaded = yaml.safe_load(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return cast("dict[str, Any]", loaded)


class AnomalyDetectionGraphNode(GraphNode):
    """GraphNode subclass assigned to the `main` slot of MfgC2010Agent.

    Wraps DomainWorkflowGraph (inner Cat 2 BaseGraph).
    Called by AgentBaseGraph backbone after pre_process and before post_process.

    Contracts:
      get_subgraph()  - instantiate and return DomainWorkflowGraph
      extract_input() - pull validated_input from outer state
      merge_output()  - map sub_result fields into outer state delta (changed keys only)
      error_strategy  - "propagate": re-raise inner errors as SubgraphError (fail-fast)
    """

    # "propagate": re-raise inner graph exceptions as SubgraphError (default — fail fast).
    # "handle": call on_subgraph_error() instead — use for graceful degradation.
    error_strategy: ClassVar[str] = "propagate"

    # False: HITL interrupts are handled inside the inner graph only.
    # True: surface inner HITL interrupt to the outer caller.
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self) -> "DomainWorkflowGraph":
        """Instantiate and return the inner domain workflow graph.

        DomainWorkflowGraph is imported lazily (inside the method) to avoid
        circular-import risk at module load time and to match the Cat 2 pattern.

        The declared detection settings are forwarded here: a node's contract is
        ``execute(self, state) -> dict``, so a node never receives a
        per-invocation config argument and the inner graph's seeded initial
        state is the only live route from config/config.yaml to a node.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def execute(self, state: AgentState) -> dict[str, Any]:
        """Skip the inner graph when the request was already found unacceptable.

        A request declined by pre_process has no validated input to act on, so
        running the inner graph would only produce a second, vaguer reason for
        the same rejection - and overwrite the specific one already settled.
        """
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        result: dict[str, Any] = super().execute(state)
        return result

    def extract_input(self, state: AgentState) -> str:
        """Return the string input passed into inner_graph.invoke().

        PreProcessNode validates the raw user_input and writes the result to
        validated_input. Prefer that; fall back to user_input if
        validated_input is absent (e.g. in unit tests).
        """
        return cast(str, state.get("validated_input", state.get("user_input", "")))

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        """Map inner graph sub_result back into the outer state delta.

        sub_result is the dict returned by DomainWorkflowGraph.get_output().
        Returns ONLY changed keys — never the full state.

        Key coupling (designed together with DomainWorkflowGraph.get_output()):
          Inner get_output() emits  -> "output", "anomaly_flag", ...
          This merge_output() reads -> sub_result.get("output"), ...

        "result": PostProcessNode (outer post_process slot) reads state["result"]
          and runs the output gate on it. The inner graph emits the rendered
          structured result under "output", so map it to "result" here —
          otherwise the gated/surfaced output is always empty.
        """
        return {
            # Outer reason wins: a reason settled before the inner run is the real
            # one, and a plain sub_result.get() would erase it.
            "error_code": state.get("error_code") or sub_result.get("error_code", ""),
            "result": sub_result.get("output"),
            "equipment_id": sub_result.get("equipment_id"),
            "anomaly_flag": sub_result.get("anomaly_flag"),
            "anomaly_score": sub_result.get("anomaly_score"),
            "anomaly_narrative": sub_result.get("anomaly_narrative"),
            "probable_cause": sub_result.get("probable_cause"),
            "recommended_action": sub_result.get("recommended_action"),
            "status": sub_result.get("status"),
        }

    def _parent_config(self) -> dict[str, Any]:
        """Forward the declared detection settings to the inner graph.

        Reads config/config.yaml (see runtime_config) and returns a flat
        settings mapping that DomainWorkflowGraph seeds into the inner graph's
        initial state, where the domain nodes read it.

        Every value is validated here (type, finiteness, range). A key that is
        absent or invalid is simply not forwarded, and the consuming node falls
        back to its own documented default. Only scalar tuning parameters travel
        this way; no configured string reaches the rendered report.
        """
        cfg = runtime_config()
        anomaly_raw = cfg.get("anomaly")
        anomaly: dict[str, Any] = anomaly_raw if isinstance(anomaly_raw, dict) else {}
        ingestion_raw = cfg.get("ingestion")
        ingestion: dict[str, Any] = ingestion_raw if isinstance(ingestion_raw, dict) else {}
        baseline_raw = cfg.get("baseline")
        baseline: dict[str, Any] = baseline_raw if isinstance(baseline_raw, dict) else {}

        declared: dict[str, Any] = {}

        warning_threshold = finite_in_range(anomaly.get("warning_threshold"), *_THRESHOLD_BOUNDS)
        if warning_threshold is not None:
            declared["warning_threshold"] = warning_threshold

        alert_threshold = finite_in_range(anomaly.get("alert_threshold"), *_THRESHOLD_BOUNDS)
        if alert_threshold is not None:
            declared["alert_threshold"] = alert_threshold

        zscore_saturation = finite_in_range(anomaly.get("zscore_saturation"), *_ZSCORE_SATURATION_BOUNDS)
        if zscore_saturation is not None:
            declared["zscore_saturation"] = zscore_saturation

        min_samples = finite_in_range(ingestion.get("min_samples"), *_MIN_SAMPLES_BOUNDS)
        if min_samples is not None:
            declared["min_samples"] = int(min_samples)

        baseline_path = baseline.get("path")
        if isinstance(baseline_path, str) and baseline_path:
            declared["baseline_path"] = baseline_path

        return declared


class MfgC2010Agent(AgentBaseGraph):
    """Outer graph for MFG-C2-010 (Cat 2).

    Inherits AgentBaseGraph directly. Domain logic is fully
    encapsulated in AnomalyDetectionGraphNode (main slot), which delegates to
    DomainWorkflowGraph (inner BaseGraph).

    Backbone (fixed — identical to Cat 1):
        START -> initialize -> pre_process -> main -> post_process -> finalize -> END

    register_nodes() is the ONLY override besides get_output():
      - super().register_nodes() fills: initialize, finalize (framework defaults)
      - pre_process:  PreProcessNode  (VERIFIED_EXTERNAL — the caller boundary)
      - main:         AnomalyDetectionGraphNode (delegates to DomainWorkflowGraph)
      - post_process: PostProcessNode (ANONYMOUS — the output boundary always runs)

    add_edges() is NOT overridden — backbone wiring belongs to the framework.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with the agent registry."""
        return "ManufacturingEquipmentAnomalyDetectionAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default InitializeNode (sets schema_version, session_id,
        trust_level) and FinalizeNode (builds response_metadata, total_time_ms).
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = AnomalyDetectionGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.

    def get_output(self, state: AgentState) -> dict[str, Any]:
        """Surface the anomaly report only when the output boundary passed it.

        The inherited implementation resolves the surfaced value as
        ``formatted_output or result``. ``result`` is written by
        AnomalyDetectionGraphNode.merge_output() as soon as the inner workflow
        returns, and the backbone skips the post_process slot on any non-success
        status — so the inherited fallback surfaces a report that the output
        boundary never inspected, on exactly the runs that failed.

        For an equipment-anomaly report that is worse than returning nothing:
        the partially-built document renders the "NORMAL / CONTINUE" defaults,
        which reads as a clean bill of health for an analysis that did not
        complete.

        On any non-success status the fallback is therefore dropped: only
        formatted_output — the value PostProcessNode wrote after gating — can be
        surfaced, and its absence surfaces nothing.
        """
        output: dict[str, Any] = super().get_output(state)
        # A run that completed WITHOUT carrying out the request holds the
        # sentence saying what to correct, not a product: none of the
        # structured fields below were produced, so none is released.
        if state.get("error_code"):
            return output
        if state.get("status") != AgentStatus.SUCCESS.value:
            surfaced: Optional[str] = state.get("formatted_output")
            output["output"] = surfaced or None
        return output


# Back-compat alias — config/agent.yaml declares the dotted class path.
Graph = MfgC2010Agent
