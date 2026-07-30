from dataclasses import replace
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from research.prototype.phase2.universe import HistoricalUniverseInputProvenance
from research.prototype.phase2.universe_contracts import HistoricalUniverse, UniverseMembership
from research.prototype.phase2.universe_gate import (
    build_universe_gate_audit_payload,
    build_universe_gate_records,
    require_complete_strict_universe,
)


def _universe() -> HistoricalUniverse:
    membership = UniverseMembership(
        index_code="000300.XSHG",
        symbol="AAA",
        effective_from=date(2024, 1, 2),
        effective_to=date(2024, 1, 2),
        source_name="fixture_archive",
        source_reference="archive://fixture/csi300",
        retrieved_at=datetime(2026, 7, 30, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        content_hash="a" * 64,
        pit_status="eligible",
    )
    return HistoricalUniverse((membership,), evidence_quality="strict_pit")


def test_gate_marks_missing_decision_date_and_stops_before_model_work() -> None:
    records = build_universe_gate_records(
        universe=_universe(),
        index_code="000300.XSHG",
        decision_dates=(date(2024, 1, 2), date(2024, 1, 3)),
        decision_cutoff_time=time(15, 30),
        timezone_name="Asia/Shanghai",
    )

    assert [record.status for record in records] == ["strict_pit", "unavailable_pit_universe"]
    assert records[0].members == ("AAA",)
    with pytest.raises(ValueError, match="strict universe coverage failed: 2024-01-03"):
        require_complete_strict_universe(records)


def test_gate_rejects_effective_date_unverified_input() -> None:
    degraded = HistoricalUniverse(
        tuple(replace(row, pit_status="effective_date_unverified") for row in _universe().memberships),
        accepted_pit_statuses=frozenset({"effective_date_unverified"}),
        evidence_quality="effective_date_unverified",
    )

    with pytest.raises(ValueError, match="strict_pit"):
        build_universe_gate_records(
            universe=degraded,
            index_code="000300.XSHG",
            decision_dates=(date(2024, 1, 2),),
            decision_cutoff_time=time(15, 30),
            timezone_name="Asia/Shanghai",
        )


def test_gate_audit_payload_preserves_hashes_and_all_decision_dates() -> None:
    records = build_universe_gate_records(
        universe=_universe(),
        index_code="000300.XSHG",
        decision_dates=(date(2024, 1, 2),),
        decision_cutoff_time=time(15, 30),
        timezone_name="Asia/Shanghai",
    )

    payload = build_universe_gate_audit_payload(
        provenance=HistoricalUniverseInputProvenance(
            input_csv_sha256="a" * 64,
            source_archive_sha256="b" * 64,
            source_archive_reference="archive://fixture/csi300",
            retrieved_at=datetime(2026, 7, 30, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            timezone_name="Asia/Shanghai",
        ),
        records=records,
    )

    assert payload["schema_version"] == "phase2-universe-gate-v1"
    assert payload["evidence_quality"] == "strict_pit"
    assert payload["input_csv_sha256"] == "a" * 64
    assert payload["source_archive_sha256"] == "b" * 64
    assert payload["decision_dates"][0]["as_of"] == date(2024, 1, 2)
