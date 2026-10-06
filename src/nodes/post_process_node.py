"""AgentCore Platform v1.0"""

# MFG-C2-010 — PostProcessNode (outer post_process slot; the output boundary)
#
# Reads the formatted anomaly result from state["result"], which is populated by
# AnomalyDetectionGraphNode.merge_output() (mapped from the inner graph's
# get_output()), and surfaces it as the finalized output AFTER the output
# boundary has passed it.
#
# Two independent checks run over the rendered report:
#
#   1. Credential content. The framework's own detector is used rather than a
#      local pattern list. A local list that is narrower than the framework's is
#      not merely incomplete: the framework re-scans this node's return value,
#      and a value it catches that this node missed makes it raise INSIDE the
#      node wrapper, which discards this node's whole delta -- including the
#      clearing below. A detector gap is therefore a containment bypass, not a
#      missed finding. Equipment-specific shapes the framework does not model
#      (control-protocol endpoints, credential assignments) are added on top.
#
#   2. Report schema. The report is an aggregate document by contract; the key
#      set is checked against the renderer's declared fields so a field added
#      without review is refused rather than shipped.
#
# On a violation the node returns ERROR and CLEARS every output-bearing field.
# Clearing matters more than the status: the surfaced value is resolved as
# `formatted_output or result`, so a falsy placeholder re-opens the fallback to
# the un-gated report. The placeholder written here is deliberately non-empty.
#
# Violation messages name the check that fired and, for a schema violation, the
# offending FIELD NAME -- never the matched value. The framework re-scans every
# value this node returns, so quoting a matched credential would make that scan
# raise and discard the clearing.

import json
import logging
import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.security.credential_detector import detect_credentials
from shared.utils.audit_logger import emit_trace_event
from src.services.failure_message import EMPTY_INPUT, INPUT_REJECTED, INVALID_VALUE, TOO_LONG

from src.graph.domain_workflow_graph import REPORT_FIELDS

logger = logging.getLogger(__name__)

# Equipment-domain shapes the framework's credential detector does not model.
# These are ADDITIONS to it, never a replacement.
_DOMAIN_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    # Control-protocol endpoint URIs (topology / credential leak).
    ("equipment_endpoint", re.compile(r"\b(?:opc\.tcp|modbus|s7|ethernet-ip)://\S{4,}", re.IGNORECASE)),
    # Credential assignment patterns, including equipment connection strings.
    (
        "credential_assignment",
        re.compile(
            r"\b(?:password|passwd|secret|api_key|token|access_key|private_key|"
            r"connection_string|conn_str)\s*[:=]\s*\S{8,}",
            re.IGNORECASE,
        ),
    ),
]

# Non-empty on purpose: the surfaced value is `formatted_output or result`, so a
# falsy placeholder would fall through to the un-gated report.
_WITHHELD_NOTICE = (
    "[Anomaly report withheld: the output boundary refused it. "
    "Re-run the analysis without credential-like strings or raw equipment endpoints.]"
)

# Fields that carry report text or a payload. All are cleared on a violation.
#
# The remaining domain fields -- equipment_id, anomaly_flag, anomaly_score,
# recommended_action -- are inert provenance: an identifier already restricted
# to an inert token, two closed enums and a bounded float. They are left in
# place deliberately, and the inventory below is pinned by a test against the
# State annotations so a future free-text field cannot quietly join them.
_OUTPUT_BEARING_FIELDS = (
    "result",
    "anomaly_narrative",
    "probable_cause",
)

# Domain State fields that are NOT output-bearing, with the reason each is inert.
# Together with _OUTPUT_BEARING_FIELDS this must account for every domain field.
_INERT_DOMAIN_FIELDS = (
    "equipment_id",  # inert token, [A-Za-z0-9._-]{2,64}
    "anomaly_flag",  # closed enum
    "anomaly_score",  # bounded float
    "recommended_action",  # closed enum
)


def _scan_content(content: str) -> Optional[str]:
    """Return the name of the first content violation, or None if clean."""
    if detect_credentials(content):
        return "credential"
    for name, pattern in _DOMAIN_PATTERNS:
        if pattern.search(content):
            return name
    return None


def _scan_schema(content: str) -> Optional[str]:
    """Return a schema violation description, or None if the report conforms.

    The report is a JSON object carrying exactly the renderer's declared fields.
    An unparseable or unexpected shape is refused: this node cannot vouch for a
    document it does not recognise.
    """
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return "the report is not a JSON document"
    if not isinstance(parsed, dict):
        return "the report is not a JSON object"
    unexpected = sorted(set(parsed) - set(REPORT_FIELDS))
    if unexpected:
        # Field NAMES only. A name is echoed only when it is an inert token.
        named = ", ".join(f if f.isidentifier() and len(f) <= 32 else "<masked>" for f in unexpected)
        return f"the report carries undeclared field(s): {named}"
    missing = sorted(set(REPORT_FIELDS) - set(parsed))
    if missing:
        return f"the report is missing declared field(s): {', '.join(missing)}"
    return None


# Reason code -> the sentence the caller reads. A code with no entry falls
# back to the generic one rather than leaking the code itself.
_DEGRADED_MESSAGES = {
    "EMPTY_INPUT": EMPTY_INPUT,
    "QUESTION_TOO_LONG": TOO_LONG,
    "INVALID_REQUEST": INVALID_VALUE,
}


class PostProcessNode(FunctionNode):
    """Output boundary: the anomaly report is checked before it is surfaced.

    Outer backbone post_process slot. Reads state["result"] (the merged
    formatted result from AnomalyDetectionGraphNode.merge_output()) and applies
    the output boundary before the response is returned to the caller.

    The trust level is ANONYMOUS deliberately. A higher requirement would make
    the boundary the first thing skipped on a low-trust call, on precisely the
    runs where the report has already been assembled -- the gate must be the one
    node that always runs.

    Input state keys:
        result: formatted anomaly result (from merge_output)

    Output state keys (partial dict):
        formatted_output:   the report when it passes; a non-empty withheld
                            notice on a violation
        result:             unchanged when clean; cleared on a violation
        anomaly_narrative,
        probable_cause:     cleared on a violation
        status:             AgentStatus.SUCCESS or AgentStatus.ERROR
        error_log:          (on a violation) the check that fired
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> dict[str, Any]:
        # A run declined upstream has nothing to format. Render the reason as
        # the caller-facing body and carry the marker onward.
        marker = state.get("error_code")
        if marker:
            message = _DEGRADED_MESSAGES.get(marker, INPUT_REJECTED)
            emit_trace_event("post_process_degraded", {"reason": marker}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": marker,
                "formatted_output": message,
                "result": message,
            }
        raw = state.get("result")

        if raw is None or (isinstance(raw, str) and not raw.strip()):
            # No anomaly report was produced. There is nothing to gate and
            # nothing to surface; the run is not failed by the absence.
            emit_trace_event("anomaly_report_absent", {"result_chars": 0}, state)
            return {
                "formatted_output": None,
                "status": AgentStatus.SUCCESS.value,
            }

        # A non-string report is refused rather than skipped. Skipping would
        # leave it in state, and the surfaced value falls back to state["result"]
        # whenever formatted_output is falsy -- so "not a string" must clear.
        if not isinstance(raw, str):
            violation: Optional[str] = "the report is not a text document"
            result = ""
        else:
            result = raw
            violation = _scan_content(result) or _scan_schema(result)
        if violation:
            logger.error("PostProcessNode: report withheld by the output boundary (%s)", violation)
            emit_trace_event(
                "anomaly_report_withheld",
                {"violation": violation, "result_chars": len(result)},
                state,
            )
            cleared: Dict[str, Any] = {field: None for field in _OUTPUT_BEARING_FIELDS}
            cleared["formatted_output"] = _WITHHELD_NOTICE
            cleared["status"] = AgentStatus.ERROR.value
            cleared["error_log"] = [f"PostProcessNode: report withheld - {violation}"]
            return cleared

        # Clean — domain audit: record that a finalized report was emitted.
        emit_trace_event(
            "anomaly_report_formatted",
            {"result_chars": len(result)},
            state,
        )

        return {
            "formatted_output": result,
            "status": AgentStatus.SUCCESS.value,
        }
