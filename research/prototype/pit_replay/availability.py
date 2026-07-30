"""Point-in-time availability rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import DisclosureObservation


@dataclass(frozen=True)
class AvailabilityDecision:
    """Whether one observation is safe to include at a decision cutoff."""

    included: bool
    reason: str


def is_disclosure_visible(
    observation: DisclosureObservation, cutoff: datetime
) -> AvailabilityDecision:
    """Apply strict timestamp and conservative date-only disclosure visibility."""

    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("cutoff must be timezone-aware")
    if observation.published_at is not None:
        if observation.published_at <= cutoff:
            return AvailabilityDecision(True, "published_at_before_cutoff")
        return AvailabilityDecision(False, "published_at_after_cutoff")
    if observation.published_date is not None:
        if observation.published_date < cutoff.date():
            return AvailabilityDecision(True, "date_only_visible_after_publish_date")
        return AvailabilityDecision(False, "date_only_wait_next_session")
    return AvailabilityDecision(False, "unavailable_pit_metadata")
