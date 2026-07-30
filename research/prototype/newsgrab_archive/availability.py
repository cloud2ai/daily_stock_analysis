"""Conservative point-in-time rules for archived NewsGrab articles."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .models import Availability


def classify_strict_pit(
    raw_published_date: Any,
    *,
    archived_at: datetime,
    decision_cutoff: datetime,
) -> Availability:
    """Classify article availability without inventing an unknown publish time."""

    _require_aware("archived_at", archived_at)
    _require_aware("decision_cutoff", decision_cutoff)
    published_at, precision, parse_status = _parse_published_at(raw_published_date)

    if parse_status == "missing":
        return Availability(
            "unavailable_missing_published_at",
            None,
            "missing",
            "published_at_missing",
        )
    if parse_status == "invalid":
        return Availability(
            "unavailable_invalid_published_at",
            None,
            "invalid",
            "published_at_invalid_or_timezone_missing",
        )
    if precision == "date_only":
        return Availability(
            "unavailable_date_only",
            None,
            "date_only",
            "published_at_date_only_requires_next_session_rule",
        )
    assert published_at is not None
    if published_at > decision_cutoff:
        return Availability(
            "unavailable_published_after_cutoff",
            published_at,
            "exact",
            "published_at_after_decision_cutoff",
        )
    if archived_at > decision_cutoff:
        return Availability(
            "unavailable_archived_after_cutoff",
            published_at,
            "exact",
            "archived_at_after_decision_cutoff",
        )
    return Availability("eligible", published_at, "exact", "published_and_archived_before_cutoff")


def _parse_published_at(raw_value: Any) -> tuple[Optional[datetime], str, str]:
    if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
        return None, "missing", "missing"
    if isinstance(raw_value, datetime):
        if raw_value.tzinfo is None or raw_value.utcoffset() is None:
            return None, "invalid", "invalid"
        return raw_value.astimezone(timezone.utc), "exact", "exact"
    if not isinstance(raw_value, str):
        return None, "invalid", "invalid"
    value = raw_value.strip()
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        return None, "date_only", "date_only"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, "invalid", "invalid"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, "invalid", "invalid"
    return parsed.astimezone(timezone.utc), "exact", "exact"


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
