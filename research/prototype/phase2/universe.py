"""Load a strict, timestamp-bounded historical constituent universe."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from research.prototype.phase2.universe_contracts import HistoricalUniverse, UniverseMembership


_CSV_NAME = "historical_index_membership.csv"
_MANIFEST_NAME = "historical_index_membership_manifest.json"
_SCHEMA_VERSION = "phase2-historical-universe-input-v1"


@dataclass(frozen=True)
class HistoricalUniverseInputProvenance:
    input_csv_sha256: str
    source_archive_sha256: str
    source_archive_reference: str
    retrieved_at: datetime
    timezone_name: str


@dataclass(frozen=True)
class StrictHistoricalUniverse:
    universe: HistoricalUniverse
    provenance: HistoricalUniverseInputProvenance


@dataclass(frozen=True)
class _EvidenceRow:
    index_code: str
    symbol: str
    announced_at: datetime
    effective_from: date
    effective_to: date
    source_name: str
    source_reference: str


def _text(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must not be blank")
    return result


def _timestamp(value: object, field: str) -> datetime:
    try:
        result = datetime.fromisoformat(_text(value, field))
    except ValueError as exc:
        raise ValueError(f"invalid {field}") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{field} must include timezone")
    return result


def _day(value: object, field: str) -> date:
    try:
        return date.fromisoformat(_text(value, field))
    except ValueError as exc:
        raise ValueError(f"invalid {field}") from exc


def _sha256(value: object, field: str) -> str:
    result = _text(value, field)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return result


def _manifest(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing manifest: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid manifest JSON: {path.name}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError("unexpected manifest schema_version")
    return payload


def _rows(csv_path: Path, expected_index_code: str) -> tuple[_EvidenceRow, ...]:
    required = {
        "index_code", "symbol", "announced_at", "effective_from", "effective_to", "source_name", "source_reference",
    }
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or set(reader.fieldnames) != required:
            raise ValueError("membership CSV must contain exactly the required columns")
        parsed = []
        for row in reader:
            index_code = _text(row.get("index_code"), "index_code")
            if index_code != expected_index_code:
                raise ValueError("unexpected membership index code")
            start = _day(row.get("effective_from"), "effective_from")
            end = _day(row.get("effective_to"), "effective_to")
            if end < start:
                raise ValueError("effective_to must not be before effective_from")
            parsed.append(
                _EvidenceRow(
                    index_code=index_code,
                    symbol=_text(row.get("symbol"), "symbol"),
                    announced_at=_timestamp(row.get("announced_at"), "announced_at"),
                    effective_from=start,
                    effective_to=end,
                    source_name=_text(row.get("source_name"), "source_name"),
                    source_reference=_text(row.get("source_reference"), "source_reference"),
                )
            )
    if not parsed:
        raise ValueError("membership CSV must not be empty")
    result = tuple(sorted(parsed, key=lambda item: (item.index_code, item.symbol, item.effective_from, item.effective_to)))
    for earlier, later in zip(result, result[1:]):
        if earlier.index_code == later.index_code and earlier.symbol == later.symbol and later.effective_from <= earlier.effective_to:
            raise ValueError("overlapping membership intervals")
    return result


def _visible_interval(row: _EvidenceRow, cutoff_time: time, timezone_name: str) -> tuple[date, date] | None:
    calendar = xcals.get_calendar("XSHG")
    sessions = tuple(
        stamp.date()
        for stamp in calendar.sessions_in_range(pd.Timestamp(row.effective_from), pd.Timestamp(row.effective_to))
    )
    if not sessions:
        raise ValueError("membership interval does not contain an XSHG session")
    first_visible = next(
        (
            session
            for session in sessions
            if datetime.combine(session, cutoff_time, tzinfo=ZoneInfo(timezone_name)) >= row.announced_at
        ),
        None,
    )
    return None if first_visible is None else (first_visible, sessions[-1])


def load_strict_historical_universe(
    input_root: Path,
    *,
    expected_index_code: str,
    decision_cutoff_time: time,
    timezone_name: str,
) -> StrictHistoricalUniverse:
    """Return only member intervals whose evidence was visible by each session cutoff."""

    if decision_cutoff_time.tzinfo is not None:
        raise ValueError("decision_cutoff_time must not include timezone")
    ZoneInfo(timezone_name)
    root = Path(input_root)
    csv_path = root / _CSV_NAME
    manifest = _manifest(root / _MANIFEST_NAME)
    actual_hash = sha256(csv_path.read_bytes()).hexdigest()
    if actual_hash != _sha256(manifest.get("csv_sha256"), "csv_sha256"):
        raise ValueError("membership CSV hash does not match manifest")
    provenance = HistoricalUniverseInputProvenance(
        input_csv_sha256=actual_hash,
        source_archive_sha256=_sha256(manifest.get("source_archive_sha256"), "source_archive_sha256"),
        source_archive_reference=_text(manifest.get("source_archive_reference"), "source_archive_reference"),
        retrieved_at=_timestamp(manifest.get("retrieved_at"), "retrieved_at"),
        timezone_name=_text(manifest.get("timezone"), "timezone"),
    )
    if provenance.timezone_name != timezone_name:
        raise ValueError("manifest timezone must match timezone_name")
    memberships = []
    for row in _rows(csv_path, expected_index_code):
        interval = _visible_interval(row, decision_cutoff_time, timezone_name)
        if interval is None:
            continue
        memberships.append(
            UniverseMembership(
                index_code=row.index_code,
                symbol=row.symbol,
                effective_from=interval[0],
                effective_to=interval[1],
                source_name=row.source_name,
                source_reference=row.source_reference,
                retrieved_at=provenance.retrieved_at,
                content_hash=actual_hash,
                pit_status="eligible",
            )
        )
    return StrictHistoricalUniverse(
        universe=HistoricalUniverse(
            tuple(memberships), accepted_pit_statuses=frozenset({"eligible"}), evidence_quality="strict_pit"
        ),
        provenance=provenance,
    )
