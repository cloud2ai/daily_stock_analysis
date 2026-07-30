"""Fail-closed decision-date coverage checks for the strict Phase 2 universe."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from research.prototype.phase2.universe import HistoricalUniverseInputProvenance
from research.prototype.phase2.universe_contracts import HistoricalUniverse


_STRICT_PIT = "strict_pit"
_UNAVAILABLE = "unavailable_pit_universe"


@dataclass(frozen=True)
class UniverseGateRecord:
    as_of: date
    decision_cutoff: datetime
    expected_index_code: str
    members: tuple[str, ...]
    status: str
    evidence_quality: str


def build_universe_gate_records(
    *,
    universe: HistoricalUniverse,
    index_code: str,
    decision_dates: Sequence[date],
    decision_cutoff_time: time,
    timezone_name: str,
) -> tuple[UniverseGateRecord, ...]:
    """Return one strict-coverage outcome per requested decision date."""

    if universe.evidence_quality != _STRICT_PIT:
        raise ValueError("universe evidence_quality must be strict_pit")
    if decision_cutoff_time.tzinfo is not None:
        raise ValueError("decision_cutoff_time must not include timezone")
    timezone = ZoneInfo(timezone_name)
    records = []
    for as_of in decision_dates:
        members = universe.members_on(index_code, as_of)
        records.append(
            UniverseGateRecord(
                as_of=as_of,
                decision_cutoff=datetime.combine(as_of, decision_cutoff_time, tzinfo=timezone),
                expected_index_code=index_code,
                members=members,
                status=_STRICT_PIT if members else _UNAVAILABLE,
                evidence_quality=universe.evidence_quality,
            )
        )
    return tuple(records)


def require_complete_strict_universe(records: Sequence[UniverseGateRecord]) -> None:
    """Stop before model work when any requested decision date lacks members."""

    for record in records:
        if record.status != _STRICT_PIT:
            raise ValueError(f"strict universe coverage failed: {record.as_of.isoformat()}")


def build_universe_gate_audit_payload(
    *,
    provenance: HistoricalUniverseInputProvenance,
    records: Sequence[UniverseGateRecord],
) -> Mapping[str, Any]:
    """Build a JSON-ready provenance payload after fail-closed coverage validation."""

    if not records:
        raise ValueError("strict universe coverage failed: no decision dates")
    require_complete_strict_universe(records)
    if any(record.evidence_quality != _STRICT_PIT for record in records):
        raise ValueError("all universe gate records must have strict_pit evidence")
    return {
        "schema_version": "phase2-universe-gate-v1",
        "evidence_quality": _STRICT_PIT,
        "input_csv_sha256": provenance.input_csv_sha256,
        "source_archive_sha256": provenance.source_archive_sha256,
        "source_archive_reference": provenance.source_archive_reference,
        "retrieved_at": provenance.retrieved_at,
        "timezone": provenance.timezone_name,
        "decision_dates": [asdict(record) for record in records],
    }
