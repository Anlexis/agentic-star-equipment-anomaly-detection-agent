"""AgentCore Platform v1.0"""

# MFG-C2-010 — BaselineLoadNode
# Inner domain node 2: retrieve the normal operating profile (per-sensor mean /
# stddev / control limits) for the equipment under analysis.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).
#
# GraphNode boundary note: the outer AgentBaseGraph.invoke() builds a FRESH
# inner-graph initial state seeded only with the string returned by
# extract_input() (stored as user_input) plus the standard framework fields
# (session_id, caller_trust_level, …).  Scalar outer-state fields such as
# equipment_id written by PreProcessNode are NOT propagated into the inner
# state.  This node therefore re-derives equipment_id from the validated JSON
# payload (validated_input / user_input) when the scalar is absent.
# SensorDataIngestionNode uses the identical fallback pattern.

import json
import logging
import os
from typing import Any, ClassVar, Dict, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.progress import emit_progress
from src.services.failure_message import INPUT_REJECTED
from src.schemas.state import to_json
from src.services.service import inert_identifier

logger = logging.getLogger(__name__)

# Directory (relative to repo root or absolute) holding per-equipment baseline
# files named "<equipment_id>.json". Overridden by the declared `baseline.path`.
_DEFAULT_BASELINE_PATH = "baselines/"

# Built-in fallback baselines so the node is runnable without on-disk files
# (smoke checks). Keyed by equipment_id. Real deployments ship JSON
# files under equipment_baseline_path.
_BUILTIN_BASELINES: Dict[str, Dict[str, Any]] = {
    "CNC-LATHE-014": {
        "vibration_mm_s": {"mean": 2.0, "stddev": 0.5, "ucl": 4.0, "lcl": 0.0},
        "spindle_temp_c": {"mean": 55.0, "stddev": 4.0, "ucl": 75.0, "lcl": 20.0},
        "spindle_current_a": {"mean": 12.0, "stddev": 1.5, "ucl": 18.0, "lcl": 4.0},
    },
}


def _load_baseline_file(baseline_path: str, equipment_id: str) -> Optional[Dict[str, Any]]:
    """Load a per-equipment baseline JSON file, if one exists on disk.

    Returns the parsed dict, or None if the file is absent / unreadable.
    """
    candidate = os.path.join(baseline_path, f"{equipment_id}.json")
    if not os.path.isfile(candidate):
        return None
    try:
        with open(candidate, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _equipment_id_from_payload(state: AgentState) -> str:
    """Re-derive equipment_id from the validated sensor JSON payload.

    Called when the scalar state["equipment_id"] is absent — this happens
    inside the inner graph because the GraphNode boundary only seeds the inner
    initial state with user_input (= extract_input() output) and framework
    fields.  PreProcessNode's scalar equipment_id write stays on the outer
    state and does not cross into the inner fresh state.

    Mirrors the validated_input / user_input fallback used by
    SensorDataIngestionNode.execute() for the same reason.
    """
    raw = state.get("validated_input") or state.get("user_input", "")
    if not raw:
        return ""
    try:
        payload = json.loads(raw) if isinstance(raw, str) else {}
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return inert_identifier(payload.get("equipment_id")) or ""


class BaselineLoadNode(FunctionNode):
    """Load the normal operating profile for the target equipment.

    Input state keys:
        equipment_id:    identifier set by PreProcessNode (outer state).
                         When absent inside the inner graph (GraphNode boundary
                         does not propagate outer scalars), falls back to
                         parsing the JSON payload from validated_input /
                         user_input.

    Output state keys (partial dict):
        baseline_profile: JSON string of {sensor_id: {mean,stddev,ucl,lcl}}
        status/error_log: ERROR when no baseline exists for equipment_id
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> dict[str, Any]:
        # A reason settled earlier in the run is the real one: pass it through
        # untouched instead of doing work on input that was already declined.
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        settings: Dict[str, Any] = state.get("detection_settings") or {}
        baseline_path = str(settings.get("baseline_path", _DEFAULT_BASELINE_PATH))

        # Prefer the scalar written upstream, re-validated rather than trusted:
        # it is used to build a filesystem path below and it renders into the
        # report.  Fall back to parsing the payload when running inside the inner
        # graph, where the outer scalar was not propagated across the boundary.
        equipment_id = inert_identifier(state.get("equipment_id")) or ""
        if not equipment_id:
            equipment_id = _equipment_id_from_payload(state)

        if not equipment_id:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["BaselineLoadNode: equipment_id is missing from state"],
            }

        # Prefer an on-disk baseline; fall back to the built-in profiles.
        profile = _load_baseline_file(baseline_path, equipment_id)
        if profile is None:
            profile = _BUILTIN_BASELINES.get(equipment_id)

        if profile is None:
            logger.error(
                "BaselineLoadNode: no baseline for equipment_id=%s (path=%s)",
                equipment_id,
                baseline_path,
            )
            emit_progress(INPUT_REJECTED)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "INVALID_REQUEST",
                "error_log": ["BaselineLoadNode: no baseline profile is available for the " "requested equipment_id"],
            }

        # Domain audit: a baseline profile was resolved.
        emit_trace_event(
            "baseline_loaded",
            {"equipment_id": equipment_id, "sensor_count": len(profile)},
            state,
        )

        logger.info(
            "BaselineLoadNode: loaded baseline for %s (%d sensors)",
            equipment_id,
            len(profile),
        )

        return {
            "baseline_profile": to_json(profile),
        }
