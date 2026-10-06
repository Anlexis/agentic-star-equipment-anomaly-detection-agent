"""AgentCore Platform v1.0"""

# MFG-C2-010 — SensorDataIngestionNode
# Inner domain node 1: deserialise the validated sensor payload and collapse
# each sensor's raw reading window into SUMMARY statistics. The raw time-series
# array is NEVER written to State.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).

import json
import logging
import math
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.progress import emit_progress
from src.services.failure_message import INPUT_REJECTED
from src.schemas.state import to_json
from src.services.service import finite_in_range, inert_identifier

logger = logging.getLogger(__name__)

# Minimum readings a sensor must have to be considered statistically usable.
# Sensors below this are flagged (warning) but NOT dropped — they still get a
# best-effort summary. Overridden by the declared `ingestion.min_samples`.
_DEFAULT_MIN_SAMPLES = 3

# Accepted magnitude for a single reading. A value outside it — or NaN, or an
# infinity — is dropped rather than summarised: both compare False against every
# threshold downstream, which would report the equipment as NORMAL.
_READING_VALUE_BOUNDS = (-1e12, 1e12)


def _summary_stats(values: List[float]) -> Dict[str, float]:
    """Compute min/max/mean/stddev for a list of numeric readings.

    Population standard deviation (ddof=0). Empty input yields zeros.
    """
    n = len(values)
    if n == 0:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "stddev": 0.0}
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    return {
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "mean": round(mean, 6),
        "stddev": round(math.sqrt(variance), 6),
    }


def _coerce_float(value: Any) -> Optional[float]:
    """Coerce a single sensor reading to a finite, bounded float, or None."""
    return finite_in_range(value, *_READING_VALUE_BOUNDS)


class SensorDataIngestionNode(FunctionNode):
    """Parse the sensor payload into per-sensor summary statistics.

    Input state keys:
        validated_input: validated request JSON (set by PreProcessNode)

    Output state keys (partial dict):
        per_sensor_summary: JSON string of {sensor_id: {min,max,mean,stddev,
                            sample_count,unit}} — NO raw arrays
        sensor_count:       number of distinct sensors seen
        equipment_id:       the identifier the analysis is scoped to
        per_sensor_scores:  JSON string of an empty dict (placeholder filled by
                            StatisticalAnalysisNode)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> dict[str, Any]:
        settings: Dict[str, Any] = state.get("detection_settings") or {}
        min_samples = int(settings.get("min_samples", _DEFAULT_MIN_SAMPLES))

        validated_input = state.get("validated_input") or state.get("user_input", "")

        try:
            request = json.loads(validated_input) if isinstance(validated_input, str) else {}
        except (json.JSONDecodeError, ValueError):
            emit_progress(INPUT_REJECTED)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "INVALID_REQUEST",
                "error_log": ["SensorDataIngestionNode: validated_input is not parseable JSON"],
            }

        if not isinstance(request, dict):
            emit_progress(INPUT_REJECTED)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": "INVALID_REQUEST",
                "error_log": ["SensorDataIngestionNode: request payload is not a JSON object"],
            }

        readings = request.get("readings", [])
        if not isinstance(readings, list):
            readings = []

        # Bucket raw readings per sensor (locally only — never stored in State).
        buckets: Dict[str, List[float]] = {}
        units: Dict[str, str] = {}
        for entry in readings:
            if not isinstance(entry, dict):
                continue
            sensor_id = entry.get("sensor_id")
            if not sensor_id or not isinstance(sensor_id, str):
                continue
            value = _coerce_float(entry.get("value"))
            if value is None:
                continue
            buckets.setdefault(sensor_id, []).append(value)
            unit = entry.get("unit")
            if isinstance(unit, str) and sensor_id not in units:
                units[sensor_id] = unit

        per_sensor_summary: Dict[str, Dict[str, Any]] = {}
        low_sample_sensors: List[str] = []
        for sensor_id, values in buckets.items():
            stats: Dict[str, Any] = dict(_summary_stats(values))
            stats["sample_count"] = len(values)
            stats["unit"] = units.get(sensor_id, "")
            per_sensor_summary[sensor_id] = stats
            if len(values) < min_samples:
                low_sample_sensors.append(sensor_id)

        if low_sample_sensors:
            logger.warning(
                "SensorDataIngestionNode: %d sensor(s) below min_samples=%d: %s",
                len(low_sample_sensors),
                min_samples,
                low_sample_sensors,
            )

        sensor_count = len(per_sensor_summary)

        # The identifier the report is about. The framework builds a FRESH state
        # for a subgraph invocation from the extracted input string alone, so the
        # outer scalar written by PreProcessNode does not cross the boundary and
        # is re-derived here. It is re-validated rather than trusted: on the
        # direct-call path no earlier validation is guaranteed to have run, and
        # this is the one caller string that renders into the report.
        equipment_id = inert_identifier(request.get("equipment_id")) or ""

        # Domain audit: sensor data ingested + summarized (no raw values).
        emit_trace_event(
            "sensor_data_ingested",
            {
                "sensor_count": sensor_count,
                "low_sample_sensors": len(low_sample_sensors),
            },
            state,
        )

        logger.info(
            "SensorDataIngestionNode: %d sensors summarized, %d below min_samples",
            sensor_count,
            len(low_sample_sensors),
        )

        return {
            "per_sensor_summary": to_json(per_sensor_summary),
            "sensor_count": sensor_count,
            "equipment_id": equipment_id,
            "per_sensor_scores": to_json({}),
        }
