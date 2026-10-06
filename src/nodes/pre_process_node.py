"""AgentCore Platform v1.0"""

# MFG-C2-010 -- PreProcessNode (outer pre_process slot; the caller boundary)
#
# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants -- never plain strings
#  - Read input_context via state.get("input_context", {}) -- read-only
#  - Never import from other agents
#
# Reject empty / malformed / hostile sensor payloads before the inner domain
# workflow runs. All validation is done inside execute(), so it holds when the
# node is called directly as well as when the framework calls it: the template
# owns its refusals rather than relying on a gate that a given deployment may
# not have active.
#
# Raw sensor time-series arrays are NEVER written to State -- only
# validated_input (the validated request JSON) and the extracted
# analysis_window / equipment_id are stored.

import json
import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.failure_message import INPUT_REJECTED
from src.services.progress import emit_progress

from src.schemas.state import to_json
from src.services.service import finite_in_range, inert_identifier

# Maximum accepted request-payload size (characters). Larger payloads are
# rejected at the boundary rather than parsed (oversized-input guard).
_MAX_PAYLOAD_CHARS = 200000

# Structural caps on the analysis window. A window that exceeds either bound is
# refused rather than truncated: silently analysing a subset of what the caller
# sent would produce a confident verdict about data that was never examined.
_MAX_READINGS = 5000
_MAX_SENSORS = 200

# Bounds on the caller's numeric fields. Every one of them is parsed through
# finite_in_range: NaN and the infinities parse fine via float() and then
# compare False against every threshold, so an unguarded value would classify
# equipment as NORMAL no matter what it was actually doing.
_READING_VALUE_BOUNDS = (-1e12, 1e12)
_WINDOW_MINUTES_BOUNDS = (0.0, 527040.0)  # up to one year, in minutes

# The caller's structured channel. Only these keys are read, and each is locked
# to an inert token so nothing free-form arrives on it.
_CONTEXT_KEYS = ("channel",)

# Chat-template control tokens and instruction-override phrasing. These are
# screened as a CLASS, not as a list of phrases: the token forms below carry no
# meaning in a sensor payload, and a directive that survives a markup strip is
# still a directive. The payload is screened twice -- once as received and once
# after parsing, walking keys as well as values -- because an escape sequence
# hides a token from the first pass and a strip can splice one back together
# for the second.
_INJECTION_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("chat_control_token", re.compile(r"<\|[^|>]{1,64}\|>")),
    ("chat_control_token", re.compile(r"\[/?INST]", re.IGNORECASE)),
    ("chat_control_token", re.compile(r"<</?SYS>>", re.IGNORECASE)),
    ("chat_control_token", re.compile(r"<\s*/?(?:system|user|assistant)\s*>", re.IGNORECASE)),
    (
        "instruction_override",
        re.compile(
            r"(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+)?"
            r"(?:previous|prior|above|earlier)\s+(?:instruction|prompt|rule|context)",
            re.IGNORECASE,
        ),
    ),
]

# Markup that a naive sanitizer would strip. Removing it can SPLICE a directive
# back together ("ig<b>nore all previous instructions"), so the screen runs
# against the stripped form too rather than trusting the strip to have helped.
_MARKUP_RE = re.compile(r"<[^<>]{0,64}>")

# Accepted equipment_id format -- alphanumerics, dash, underscore, dot;
# 2..64 chars. This is the only caller string that reaches the rendered report,
# so the alphabet is deliberately inert.
_EQUIPMENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{2,64}$")

# A field name is echoed back only when it is itself an inert token; anything
# else is reported positionally so a hostile key cannot ride out in an error.
_SAFE_FIELD_NAME_RE = re.compile(r"^[A-Za-z0-9_.]{1,32}$")


def _extract_request(user_input: str) -> Optional[Dict[str, Any]]:
    """Parse the request payload as a JSON object.

    Returns the parsed dict, or None if the input is not a JSON object.
    """
    try:
        parsed = json.loads(user_input)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _screen_text(text: str) -> Optional[str]:
    """Return the violation class for a hostile string, or None.

    Screens the string as given and again with markup removed.
    """
    candidates = (text, _MARKUP_RE.sub("", text))
    for candidate in candidates:
        for name, pattern in _INJECTION_PATTERNS:
            if pattern.search(candidate):
                return name
    return None


def _screen_payload(value: Any) -> Optional[str]:
    """Walk a parsed payload depth-first and screen every key and string leaf.

    Keys are screened as well as values: a directive placed in a field name
    reaches exactly the same downstream consumers as one placed in a value.
    """
    if isinstance(value, str):
        return _screen_text(value)
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str):
                violation = _screen_text(key)
                if violation:
                    return violation
            violation = _screen_payload(nested)
            if violation:
                return violation
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            violation = _screen_payload(item)
            if violation:
                return violation
    return None


def _safe_field_name(name: Any, position: int) -> str:
    """Name a rejected field only when the name is itself safe to echo."""
    if isinstance(name, str) and _SAFE_FIELD_NAME_RE.match(name) and not _screen_text(name):
        return name
    return f"field #{position}"


def _reject(message: str, code: str = "INVALID_REQUEST") -> dict[str, Any]:
    """Build the rejection delta. Messages name FIELDS, never values.

    Two ways to stop, and the caller can act on only one of them. A value the
    caller can correct completes the run carrying ``code``, so the reason
    reaches the caller and a corrected request can be sent on the same
    conversation. Content the agent refuses outright passes ``code=""`` and
    terminates, so a refusal is never presented as something a reworded
    request would get past.

    The branch is chosen by the call site through ``code``, never by reading
    the message text: the refusal sites are the ones that pass ``code=""``.
    """
    if code:
        # A value the caller can correct: the run COMPLETES carrying the
        # reason so the request can be sent again on the same conversation.
        emit_progress(INPUT_REJECTED)
        return {
            "status": AgentStatus.SUCCESS.value,
            "error_code": code,
            "error_log": [f"PreProcessNode: {message}"],
        }
    return {
        "status": AgentStatus.ERROR.value,
        "error_log": [f"PreProcessNode: {message}"],
    }


class PreProcessNode(FunctionNode):
    """Input validation for the sensor anomaly request.

    Rejects empty / invalid / hostile input before the inner domain workflow
    graph runs, extracts equipment_id + analysis_window, and writes
    validated_input. Raw sensor arrays are never persisted to State.

    All validation lives in execute() so the refusal holds when the node is
    exercised directly, with no framework wrapper in front of it.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> dict[str, Any]:
        user_input = state.get("user_input", "")

        # Empty / non-string input is rejected outright.
        if not user_input or not isinstance(user_input, str) or not user_input.strip():
            return _reject("user_input is empty or missing", code="EMPTY_INPUT")

        user_input = user_input.strip()

        # Payload size gate.
        if len(user_input) > _MAX_PAYLOAD_CHARS:
            return _reject(
                f"input rejected - payload exceeds the size limit " f"({len(user_input)} > {_MAX_PAYLOAD_CHARS} chars)",
                code="QUESTION_TOO_LONG",
            )

        # Screen the request as received, before it is parsed: a control token is
        # visible here even when the parse would later normalise it away.
        violation = _screen_text(user_input)
        if violation:
            # Terminal: a refusal, not a correctable value. Rewording the
            # request must not be presented as a route past it.
            return _reject(f"input rejected - user_input carries a {violation} pattern", code="")

        # Must be a valid JSON object.
        request = _extract_request(user_input)
        if request is None:
            return _reject("input rejected - request payload is not a JSON object")

        # Screen again after parsing, keys included: a \\u-escaped token is
        # invisible in the raw text and only assembles once JSON decodes it.
        violation = _screen_payload(request)
        if violation:
            # Terminal for the same reason as the pre-parse screen above: the
            # escaped form of a directive is the same refusal, not a value.
            return _reject(f"input rejected - the request payload carries a {violation} pattern", code="")

        # equipment_id presence + format. This value renders into the report, so
        # it is held to the inert alphabet rather than merely being non-empty.
        equipment_id = request.get("equipment_id")
        if not equipment_id or not isinstance(equipment_id, str):
            return _reject("input rejected - equipment_id is missing")
        if not _EQUIPMENT_ID_RE.match(equipment_id):
            return _reject("input rejected - equipment_id has an invalid format")

        # Structural caps: refuse an oversized window rather than analysing part
        # of it and reporting as though the whole window had been examined.
        readings = request.get("readings", [])
        if readings is not None and not isinstance(readings, list):
            return _reject("input rejected - readings must be a list")
        readings = readings or []
        if len(readings) > _MAX_READINGS:
            return _reject(f"input rejected - readings exceeds the entry limit " f"({len(readings)} > {_MAX_READINGS})")

        sensor_ids = set()
        for position, entry in enumerate(readings, start=1):
            if not isinstance(entry, dict):
                return _reject(f"input rejected - readings entry #{position} is not an object")
            sensor_id = entry.get("sensor_id")
            if inert_identifier(sensor_id) is None:
                return _reject(f"input rejected - readings entry #{position} has an invalid sensor_id")
            sensor_ids.add(sensor_id)
            if finite_in_range(entry.get("value"), *_READING_VALUE_BOUNDS) is None:
                return _reject(
                    f"input rejected - readings entry #{position} has a value that is "
                    f"not a finite number within the accepted range"
                )
            unit = entry.get("unit")
            if unit is not None and (not isinstance(unit, str) or len(unit) > 32):
                return _reject(f"input rejected - readings entry #{position} has an invalid unit")

        if len(sensor_ids) > _MAX_SENSORS:
            return _reject(f"input rejected - the payload covers more than {_MAX_SENSORS} distinct sensors")

        # Normalize the analysis window (stored as a JSON string).
        window_raw = request.get("analysis_window", {})
        if window_raw is not None and not isinstance(window_raw, dict):
            return _reject("input rejected - analysis_window must be an object")
        window_raw = window_raw or {}
        window_minutes = finite_in_range(window_raw.get("window_size_minutes", 0), *_WINDOW_MINUTES_BOUNDS)
        if window_minutes is None:
            return _reject(
                "input rejected - analysis_window.window_size_minutes is not a finite "
                "number within the accepted range"
            )
        analysis_window = {
            "start_time": str(window_raw.get("start_time", ""))[:64],
            "end_time": str(window_raw.get("end_time", ""))[:64],
            "window_size_minutes": window_minutes,
        }

        # The structured channel carries inert tokens only. Unknown keys are not
        # read, and a declared key that is not an inert token is refused rather
        # than passed through: it would travel with the request unexamined.
        raw_context = state.get("input_context") or {}
        if not isinstance(raw_context, dict):
            return _reject("input rejected - input_context must be an object")
        channel = "unknown"
        for position, key in enumerate(_CONTEXT_KEYS, start=1):
            if key not in raw_context:
                continue
            token = inert_identifier(raw_context.get(key))
            if token is None:
                return _reject(
                    f"input rejected - input_context.{_safe_field_name(key, position)} "
                    f"is not an accepted identifier"
                )
            if key == "channel":
                channel = token

        # Domain audit: a sensor-anomaly request was accepted + validated.
        # (No raw sensor values are included in the trace payload.)
        emit_trace_event(
            "sensor_payload_validated",
            {
                "equipment_id": equipment_id,
                "input_chars": len(user_input),
                "reading_count": len(readings),
                "sensor_count": len(sensor_ids),
            },
            state,
        )

        return {
            "validated_input": user_input,
            "equipment_id": equipment_id,
            "analysis_window": to_json(analysis_window),
            "enriched_context": {
                "source": "ManufacturingEquipmentAnomalyDetectionAgent",
                "channel": channel,
            },
            "status": AgentStatus.SUCCESS.value,
        }
