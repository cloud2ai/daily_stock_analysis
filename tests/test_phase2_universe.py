import csv
import json
from datetime import date, time
from hashlib import sha256
from pathlib import Path

import pytest

from research.prototype.phase2.universe import load_strict_historical_universe


def _write_input(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    input_root = tmp_path / "universe_input"
    input_root.mkdir()
    csv_path = input_root / "historical_index_membership.csv"
    fieldnames = (
        "index_code",
        "symbol",
        "announced_at",
        "effective_from",
        "effective_to",
        "source_name",
        "source_reference",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    input_root.joinpath("historical_index_membership_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "phase2-historical-universe-input-v1",
                "csv_sha256": sha256(csv_path.read_bytes()).hexdigest(),
                "retrieved_at": "2026-07-30T09:00:00+08:00",
                "source_archive_sha256": "a" * 64,
                "source_archive_reference": "archive://csi300/adjustments",
                "timezone": "Asia/Shanghai",
            }
        ),
        encoding="utf-8",
    )
    return input_root


def _row(*, announced_at: str) -> dict[str, str]:
    return {
        "index_code": "000300.XSHG",
        "symbol": "AAA",
        "announced_at": announced_at,
        "effective_from": "2024-01-02",
        "effective_to": "2024-01-05",
        "source_name": "fixture_archive",
        "source_reference": "archive://fixture/csi300",
    }


def test_strict_universe_excludes_same_day_membership_announced_after_cutoff(tmp_path: Path) -> None:
    universe = load_strict_historical_universe(
        _write_input(tmp_path, [_row(announced_at="2024-01-02T16:00:00+08:00")]),
        expected_index_code="000300.XSHG",
        decision_cutoff_time=time(15, 30),
        timezone_name="Asia/Shanghai",
    ).universe

    assert universe.evidence_quality == "strict_pit"
    assert universe.members_on("000300.XSHG", date(2024, 1, 2)) == ()
    assert universe.members_on("000300.XSHG", date(2024, 1, 3)) == ("AAA",)


def test_strict_universe_includes_membership_announced_before_cutoff(tmp_path: Path) -> None:
    universe = load_strict_historical_universe(
        _write_input(tmp_path, [_row(announced_at="2024-01-01T18:00:00+08:00")]),
        expected_index_code="000300.XSHG",
        decision_cutoff_time=time(15, 30),
        timezone_name="Asia/Shanghai",
    ).universe

    assert universe.members_on("000300.XSHG", date(2024, 1, 2)) == ("AAA",)


def test_strict_universe_rejects_csv_changed_after_manifest(tmp_path: Path) -> None:
    input_root = _write_input(tmp_path, [_row(announced_at="2024-01-01T18:00:00+08:00")])
    csv_path = input_root / "historical_index_membership.csv"
    csv_path.write_text(csv_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hash does not match"):
        load_strict_historical_universe(
            input_root,
            expected_index_code="000300.XSHG",
            decision_cutoff_time=time(15, 30),
            timezone_name="Asia/Shanghai",
        )


def test_strict_universe_rejects_announcement_without_timezone(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="announced_at must include timezone"):
        load_strict_historical_universe(
            _write_input(tmp_path, [_row(announced_at="2024-01-01T18:00:00")]),
            expected_index_code="000300.XSHG",
            decision_cutoff_time=time(15, 30),
            timezone_name="Asia/Shanghai",
        )


def test_strict_universe_rejects_overlapping_membership_intervals(tmp_path: Path) -> None:
    later_row = _row(announced_at="2024-01-01T18:00:00+08:00")
    later_row.update({"effective_from": "2024-01-04", "effective_to": "2024-01-08"})

    with pytest.raises(ValueError, match="overlapping membership intervals"):
        load_strict_historical_universe(
            _write_input(tmp_path, [_row(announced_at="2024-01-01T18:00:00+08:00"), later_row]),
            expected_index_code="000300.XSHG",
            decision_cutoff_time=time(15, 30),
            timezone_name="Asia/Shanghai",
        )


def test_strict_universe_rejects_interval_without_xshg_session(tmp_path: Path) -> None:
    weekend_row = _row(announced_at="2024-01-01T18:00:00+08:00")
    weekend_row.update({"effective_from": "2024-01-06", "effective_to": "2024-01-07"})

    with pytest.raises(ValueError, match="does not contain an XSHG session"):
        load_strict_historical_universe(
            _write_input(tmp_path, [weekend_row]),
            expected_index_code="000300.XSHG",
            decision_cutoff_time=time(15, 30),
            timezone_name="Asia/Shanghai",
        )
