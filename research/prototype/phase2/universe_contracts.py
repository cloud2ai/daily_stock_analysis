"""Auditable historical index-constituent contracts for the Phase 2 prototype."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Sequence


_ELIGIBLE = "eligible"


@dataclass(frozen=True)
class UniverseMembership:
    """One timestamped historical index-membership interval."""

    index_code: str
    symbol: str
    effective_from: date
    effective_to: date | None
    source_name: str
    source_reference: str
    retrieved_at: datetime
    content_hash: str
    pit_status: str

    def __post_init__(self) -> None:
        for field_name in (
            "index_code",
            "symbol",
            "source_name",
            "source_reference",
            "content_hash",
            "pit_status",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be blank")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be before effective_from")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")


@dataclass(frozen=True)
class HistoricalUniverse:
    """Frozen membership relations with explicit evidence-quality metadata."""

    memberships: Sequence[UniverseMembership]
    accepted_pit_statuses: frozenset[str] = frozenset({_ELIGIBLE})
    evidence_quality: str = "strict_pit"

    def __post_init__(self) -> None:
        if not self.accepted_pit_statuses:
            raise ValueError("accepted_pit_statuses must not be empty")
        if any(not status.strip() for status in self.accepted_pit_statuses):
            raise ValueError("accepted_pit_statuses must not contain blank values")
        if not self.evidence_quality.strip():
            raise ValueError("evidence_quality must not be blank")

    def members_on(self, index_code: str, as_of: date) -> tuple[str, ...]:
        """Return eligible members effective on one replay date."""

        return tuple(
            sorted(
                {
                    row.symbol
                    for row in self.memberships
                    if row.index_code == index_code
                    and row.pit_status in self.accepted_pit_statuses
                    and row.effective_from <= as_of
                    and (row.effective_to is None or as_of <= row.effective_to)
                }
            )
        )
