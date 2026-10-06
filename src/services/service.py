"""AgentCore Platform v1.0"""

# Service layer: shared validation primitives for the anomaly-detection domain.
# Must NOT contain business logic, routing, or credentials.
#
# Every caller-supplied number reaches the pipeline through finite_in_range().
# float("nan") and float("inf") parse without raising and then compare False
# against every bound, so an unguarded comparison silently accepts them and the
# equipment classification it feeds becomes meaningless. Rejection is explicit.

from __future__ import annotations

import math
import re
from typing import Any, Optional

# Identifiers that may be rendered back into the maintenance report. Deliberately
# narrow: a value that reaches an operator-facing document is output the caller
# controls, so it is restricted to an inert token alphabet with a length bound.
_INERT_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]{2,64}$")


def finite_in_range(
    value: Any,
    minimum: float,
    maximum: float,
) -> Optional[float]:
    """Return ``value`` as a finite float inside [minimum, maximum], else None.

    Rejects booleans (``bool`` is an ``int`` subclass and ``True`` would parse as
    1.0), non-numeric types, unparseable strings, NaN, and both infinities.
    Returning None rather than raising lets each caller decide whether an
    out-of-contract value is fatal for a caller field or merely a declared
    setting falling back to its documented default.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        candidate = float(value)
    elif isinstance(value, str):
        try:
            candidate = float(value.strip())
        except (TypeError, ValueError):
            return None
    else:
        return None
    if not math.isfinite(candidate):
        return None
    if candidate < minimum or candidate > maximum:
        return None
    return candidate


def inert_identifier(value: Any) -> Optional[str]:
    """Return ``value`` when it is an inert identifier token, else None.

    Applied to every caller string that can reach the rendered report.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not _INERT_IDENTIFIER_RE.match(candidate):
        return None
    return candidate
