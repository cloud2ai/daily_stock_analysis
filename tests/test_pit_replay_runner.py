from datetime import date, time
import json
from typing import Mapping

import pandas as pd

from src.core.target_position_backtest import BacktestAssumptions
from research.prototype.pit_replay import models, runner
from research.prototype.pit_replay.audit import AuditWriter


class _PriceSource:
    def __init__(self, series_by_symbol: Mapping[str, object]) -> None:
        self._series_by_symbol = series_by_symbol

    def load(self, symbol: str, start: date, end: date) -> object:
        return self._series_by_symbol[symbol]


class _HalfTargetStrategy:
    version = "test-half-target-v1"

    def evaluate(self, snapshot: object, previous_state: Mapping[str, object]) -> object:
        return models.StrategyResult(
            state={"decisions": int(previous_state.get("decisions", 0)) + 1},
            target_weights={symbol: 0.5 for symbol in snapshot.price_history},
            reasons={symbol: "test target" for symbol in snapshot.price_history},
        )


def _assumptions() -> BacktestAssumptions:
    return BacktestAssumptions(
        initial_cash=10_000.0,
        commission_rate=0.0,
        minimum_commission=0.0,
        stamp_duty_rate=0.0,
        transfer_fee_rate=0.0,
        slippage_rate=0.0,
        annual_trading_days=242,
        annual_risk_free_rate=0.0,
        risk_free_rate_source="deterministic test fixture",
        risk_free_rate_as_of=date(2024, 1, 2),
    )


def _series(symbol: str) -> object:
    return models.PriceSeries(
        symbol=symbol,
        source_name="fixture",
        quality_status="contract_validated",
        observations=(
            models.PriceObservation(symbol, date(2024, 1, 2), 10.0, 10.0, 10.0, 10.0, 1_000),
            models.PriceObservation(symbol, date(2024, 1, 3), 10.0, 10.0, 10.0, 10.0, 1_000),
        ),
    )


def test_audit_writer_serializes_decision_cutoff_time(tmp_path) -> None:
    writer = AuditWriter(tmp_path)
    manifest_path = writer.write_json("manifest.json", {"decision_cutoff_time": time(15, 30)})

    assert json.loads(manifest_path.read_text(encoding="utf-8")) == {"decision_cutoff_time": "15:30:00"}


def test_dsa_price_source_marks_unversioned_history_degraded() -> None:
    class _Manager:
        def get_daily_data(self, symbol: str, start_date: str, end_date: str):
            return (
                pd.DataFrame(
                    [
                        {
                            "date": "2024-01-02",
                            "open": 10.0,
                            "high": 11.0,
                            "low": 9.0,
                            "close": 10.5,
                            "volume": 1_000,
                        }
                    ]
                ),
                "TencentFetcher",
            )

    series = runner.DsaDailyPriceSource(_Manager()).load(
        "600519", date(2024, 1, 1), date(2024, 1, 31)
    )

    assert series.source_name == "TencentFetcher"
    assert series.quality_status == "adjusted_price_simulation"
    assert series.observations[0].close == 10.5


def test_cli_request_uses_shanghai_cutoff_and_warmup() -> None:
    request, output_root = runner.parse_cli_request(
        [
            "--stocks", "600519,300750",
            "--start", "2024-01-02",
            "--end", "2024-03-01",
            "--decision-cutoff", "15:30:00+08:00",
            "--output-dir", "research/local/pit_replay",
        ]
    )

    assert request.symbols == ("600519", "300750")
    assert request.timezone_name == "Asia/Shanghai"
    assert request.decision_cutoff_time.isoformat() == "15:30:00"
    assert request.warmup_calendar_days == 120
    assert str(output_root) == "research/local/pit_replay"


def test_strategy_snapshot_excludes_same_day_date_only_disclosure(tmp_path) -> None:
    class _DisclosureSource:
        def load(self, symbol: str, start: date, end: date):
            return (models.DisclosureObservation(symbol, "announcement", published_date=date(2024, 1, 2)),)

    class _Strategy:
        version = "disclosure-visibility-test-v1"

        def evaluate(self, snapshot, previous_state):
            assert snapshot.disclosures["AAA"] == ()
            return models.StrategyResult(
                state={},
                target_weights={"AAA": 0.0, "BBB": 0.0},
                reasons={"AAA": "test", "BBB": "test"},
            )

    request = models.ReplayRequest(
        symbols=("AAA", "BBB"),
        start=date(2024, 1, 2),
        end=date(2024, 1, 3),
        decision_cutoff_time=time(15, 30),
        assumptions=_assumptions(),
    )

    runner.run_replay(
        request=request,
        price_source=_PriceSource({"AAA": _series("AAA"), "BBB": _series("BBB")}),
        disclosure_source=_DisclosureSource(),
        strategy=_Strategy(),
        output_root=tmp_path,
    )


def test_replay_writes_timed_audit_artifacts(tmp_path) -> None:
    request = models.ReplayRequest(
        symbols=("AAA", "BBB"),
        start=date(2024, 1, 2),
        end=date(2024, 1, 3),
        decision_cutoff_time=time(15, 30),
        assumptions=_assumptions(),
    )

    result = runner.run_replay(
        request=request,
        price_source=_PriceSource({"AAA": _series("AAA"), "BBB": _series("BBB")}),
        disclosure_source=runner.EmptyDisclosureSource(),
        strategy=_HalfTargetStrategy(),
        output_root=tmp_path,
    )

    assert result.manifest["execution_timing"] == "t_close_to_t_plus_1_open"
    assert (result.output_dir / "input_snapshots.jsonl").exists()
    assert (result.output_dir / "states.jsonl").exists()
    assert (result.output_dir / "targets.jsonl").exists()
    assert (result.output_dir / "orders.jsonl").exists()
    assert (result.output_dir / "fills.jsonl").exists()
    assert (result.output_dir / "nav.jsonl").exists()
    assert (result.output_dir / "checksums.json").exists()
    assert result.portfolio.daily_nav[-1].positions == {"AAA": 500, "BBB": 500}
