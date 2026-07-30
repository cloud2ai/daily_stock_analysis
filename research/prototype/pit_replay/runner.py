"""Automatic daily PIT replay orchestration."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from src.core.target_position_backtest import (
    BacktestAssumptions,
    DailyBar,
    PortfolioDailyTargetWeights,
    PortfolioTargetPositionBacktestResult,
    run_portfolio_target_position_backtest,
)

from .audit import AuditWriter
from .availability import is_disclosure_visible
from .models import (
    DisclosureObservation,
    PITSnapshot,
    PriceObservation,
    PriceSeries,
    ReplayRequest,
    SnapshotDisclosure,
    StrategyResult,
)


class ReplayPriceSource(Protocol):
    def load(self, symbol: str, start: date, end: date) -> PriceSeries: ...


class ReplayDisclosureSource(Protocol):
    def load(self, symbol: str, start: date, end: date) -> Sequence[DisclosureObservation]: ...


class ReplayStrategy(Protocol):
    version: str

    def evaluate(self, snapshot: PITSnapshot, previous_state: Mapping[str, Any]) -> StrategyResult: ...


class EmptyDisclosureSource:
    """Explicitly records that no historical disclosure feed is connected."""

    def load(self, symbol: str, start: date, end: date) -> Sequence[DisclosureObservation]:
        return ()


class DsaDailyPriceSource:
    """Adapt DSA's unified daily-data manager without creating another feed."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def load(self, symbol: str, start: date, end: date) -> PriceSeries:
        frame, source_name = self._manager.get_daily_data(
            symbol,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
        required = {"date", "open", "high", "low", "close", "volume"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"DSA daily data missing columns: {sorted(missing)}")
        work = frame.loc[:, ["date", "open", "high", "low", "close", "volume"]].copy()
        work["date"] = pd.to_datetime(work["date"], errors="raise").dt.date
        work = work.drop_duplicates("date").sort_values("date")
        observations = tuple(
            PriceObservation(
                symbol=symbol,
                date=row.date,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
            )
            for row in work.itertuples(index=False)
        )
        return PriceSeries(
            symbol=symbol,
            source_name=str(source_name),
            quality_status="adjusted_price_simulation",
            observations=observations,
        )


class JsonlDisclosureSource:
    """Read auditable disclosure metadata supplied by a research input file."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def load(self, symbol: str, start: date, end: date) -> Sequence[DisclosureObservation]:
        observations: list[DisclosureObservation] = []
        with self._path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if payload.get("symbol") != symbol:
                    continue
                raw_published_at = payload.get("published_at")
                raw_published_date = payload.get("published_date")
                published_at = (
                    datetime.fromisoformat(raw_published_at)
                    if raw_published_at is not None else None
                )
                published_date = (
                    date.fromisoformat(raw_published_date)
                    if raw_published_date is not None else None
                )
                try:
                    observations.append(
                        DisclosureObservation(
                            symbol=symbol,
                            kind=str(payload["kind"]),
                            published_at=published_at,
                            published_date=published_date,
                        )
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"invalid disclosure JSONL at line {line_number}: {exc}"
                    ) from exc
        return tuple(observations)


@dataclass(frozen=True)
class ReplayRunResult:
    output_dir: Path
    manifest: Mapping[str, Any]
    portfolio: PortfolioTargetPositionBacktestResult


def _cutoff_for(request: ReplayRequest, as_of: date) -> datetime:
    tz = ZoneInfo(request.timezone_name)
    return datetime.combine(as_of, request.decision_cutoff_time, tzinfo=tz)


def _serialize_snapshot(
    snapshot: PITSnapshot,
    disclosure_availability: Mapping[str, Sequence[SnapshotDisclosure]],
) -> dict[str, Any]:
    return {
        "as_of": snapshot.as_of,
        "decision_cutoff": snapshot.decision_cutoff,
        "price_history": snapshot.price_history,
        "disclosures": snapshot.disclosures,
        "disclosure_availability": disclosure_availability,
        "price_quality": snapshot.price_quality,
    }


def run_replay(
    *,
    request: ReplayRequest,
    price_source: ReplayPriceSource,
    disclosure_source: ReplayDisclosureSource,
    strategy: ReplayStrategy,
    output_root: Path,
) -> ReplayRunResult:
    """Freeze daily snapshots, calculate targets, and simulate t+1-open fills."""

    fetch_start = request.start - timedelta(days=request.warmup_calendar_days)
    series_by_symbol = {
        symbol: price_source.load(symbol, fetch_start, request.end)
        for symbol in request.symbols
    }
    all_observations_by_symbol = {
        symbol: tuple(
            item for item in series.observations if item.date <= request.end
        )
        for symbol, series in series_by_symbol.items()
    }
    observations_by_symbol = {
        symbol: tuple(item for item in observations if request.start <= item.date <= request.end)
        for symbol, observations in all_observations_by_symbol.items()
    }
    if any(not observations for observations in observations_by_symbol.values()):
        missing = sorted(symbol for symbol, values in observations_by_symbol.items() if not values)
        raise ValueError(f"price data unavailable in requested range: {missing}")
    common_dates = sorted(
        set.intersection(*(set(item.date for item in values) for values in observations_by_symbol.values()))
    )
    if len(common_dates) < 2:
        raise ValueError("at least two shared completed sessions are required for t+1 execution")

    disclosures_by_symbol = {
        symbol: tuple(disclosure_source.load(symbol, request.start, request.end))
        for symbol in request.symbols
    }
    writer = AuditWriter(Path(output_root))
    snapshots: list[dict[str, Any]] = []
    state_records: list[dict[str, Any]] = []
    target_records: list[dict[str, Any]] = []
    targets: list[PortfolioDailyTargetWeights] = []
    previous_state: Mapping[str, Any] = {}

    for as_of in common_dates[:-1]:
        cutoff = _cutoff_for(request, as_of)
        if cutoff.hour < 15:
            raise ValueError("decision_cutoff must not be before the completed A-share close")
        price_history = {
            symbol: tuple(item for item in observations if item.date <= as_of)
            for symbol, observations in all_observations_by_symbol.items()
        }
        disclosure_availability = {
            symbol: tuple(
                SnapshotDisclosure(
                    observation=item,
                    included=decision.included,
                    availability_reason=decision.reason,
                )
                for item in disclosures_by_symbol[symbol]
                for decision in (is_disclosure_visible(item, cutoff),)
            )
            for symbol in request.symbols
        }
        visible_disclosures = {
            symbol: tuple(
                row.observation
                for row in disclosure_availability[symbol]
                if row.included
            )
            for symbol in request.symbols
        }
        snapshot = PITSnapshot(
            as_of=as_of,
            decision_cutoff=cutoff,
            price_history=price_history,
            disclosures=visible_disclosures,
            price_quality={symbol: series_by_symbol[symbol].quality_status for symbol in request.symbols},
        )
        result = strategy.evaluate(snapshot, previous_state)
        if set(result.target_weights) != set(request.symbols):
            raise ValueError("strategy target weights must contain exactly the request symbols")
        targets.append(PortfolioDailyTargetWeights(as_of, dict(result.target_weights)))
        snapshots.append(_serialize_snapshot(snapshot, disclosure_availability))
        state_records.append({"as_of": as_of, "state": result.state, "strategy_version": strategy.version})
        target_records.append(
            {
                "as_of": as_of,
                "target_weights": result.target_weights,
                "reasons": result.reasons,
                "strategy_version": strategy.version,
            }
        )
        previous_state = result.state

    portfolio = run_portfolio_target_position_backtest(
        bars_by_symbol={
            symbol: [
                DailyBar(
                    date=item.date,
                    open=item.open,
                    high=item.high,
                    low=item.low,
                    close=item.close,
                    volume=item.volume,
                )
                for item in observations
            ]
            for symbol, observations in observations_by_symbol.items()
        },
        target_weights=targets,
        assumptions=request.assumptions,
    )
    writer.write_jsonl("input_snapshots", snapshots)
    writer.write_jsonl("states", state_records)
    writer.write_jsonl("targets", target_records)
    writer.write_jsonl("orders", [asdict(item) for item in portfolio.trades])
    writer.write_jsonl("fills", [asdict(item) for item in portfolio.trades if item.status == "filled"])
    writer.write_jsonl("nav", [asdict(item) for item in portfolio.daily_nav])
    writer.write_checksums()
    manifest = {
        "schema_version": "dsa-v2-stage-0a-pit-replay-v1",
        "execution_timing": "t_close_to_t_plus_1_open",
        "request": request,
        "strategy_version": strategy.version,
        "price_sources": {
            symbol: {
                "source_name": series_by_symbol[symbol].source_name,
                "quality_status": series_by_symbol[symbol].quality_status,
            }
            for symbol in request.symbols
        },
        "disclosure_source": type(disclosure_source).__name__,
        "disclosure_availability": (
            "source_absent" if isinstance(disclosure_source, EmptyDisclosureSource) else "source_provided"
        ),
        "status": "completed",
    }
    writer.write_json("manifest.json", manifest)
    return ReplayRunResult(output_dir=writer.output_dir, manifest=manifest, portfolio=portfolio)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stocks", required=True, help="comma-separated A-share symbols")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--decision-cutoff", default="15:30:00+08:00")
    parser.add_argument("--warmup-calendar-days", type=int, default=120)
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
    parser.add_argument("--output-dir", type=Path, default=Path("research/local/pit_replay"))
    parser.add_argument("--disclosures-file", type=Path)
    parser.add_argument("--strategy", default="pit-baseline-momentum20-v1")
    return parser


def _parse_cutoff_time(value: str) -> time:
    parsed = time.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(hours=8):
        raise ValueError("decision_cutoff must include the +08:00 Shanghai offset")
    return parsed.replace(tzinfo=None)


def _request_from_args(args: argparse.Namespace) -> ReplayRequest:
    symbols = tuple(item.strip() for item in args.stocks.split(",") if item.strip())
    cutoff_time = _parse_cutoff_time(args.decision_cutoff)
    return ReplayRequest(
        symbols=symbols,
        start=args.start,
        end=args.end,
        decision_cutoff_time=cutoff_time,
        warmup_calendar_days=args.warmup_calendar_days,
        assumptions=BacktestAssumptions(
            initial_cash=args.initial_cash,
            commission_rate=0.0003,
            minimum_commission=5.0,
            stamp_duty_rate=0.0005,
            transfer_fee_rate=0.00001,
            slippage_rate=0.001,
            annual_trading_days=242,
            annual_risk_free_rate=0.0,
            risk_free_rate_source="v0.2 frozen zero-risk-free-rate convention",
            risk_free_rate_as_of=args.start,
            lot_size=100,
        ),
    )


def parse_cli_request(argv: Sequence[str] | None = None) -> tuple[ReplayRequest, Path]:
    """Parse the safe, research-only CLI inputs without triggering data retrieval."""

    args = _build_cli_parser().parse_args(argv)
    return _request_from_args(args), args.output_dir


def main(argv: Iterable[str] | None = None) -> None:
    args = _build_cli_parser().parse_args(argv)
    if args.strategy != "pit-baseline-momentum20-v1":
        raise ValueError(f"unsupported frozen strategy: {args.strategy}")
    from data_provider.base import DataFetcherManager
    from .strategy import Momentum20Strategy

    request = _request_from_args(args)
    result = run_replay(
        request=request,
        price_source=DsaDailyPriceSource(DataFetcherManager()),
        disclosure_source=(
            JsonlDisclosureSource(args.disclosures_file)
            if args.disclosures_file is not None else EmptyDisclosureSource()
        ),
        strategy=Momentum20Strategy(),
        output_root=args.output_dir,
    )
    print(json.dumps({"output_dir": str(result.output_dir), "status": result.manifest["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
