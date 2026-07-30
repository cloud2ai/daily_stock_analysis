"""Deterministic A-share target-position backtest engine.

This module is deliberately independent from DSA's strategy, debate, action,
database, and provider layers.  It accepts already-frozen daily target weights
and artificial or contract-validated daily bars, then produces an auditable
cash-and-shares ledger.

v0.1 assumptions that require later sensitivity testing:
* signals formed on session ``t`` execute at session ``t+1`` open;
* slippage is a fixed adverse percentage per side;
* annualisation uses 242 A-share trading days;
* the Sharpe numerator is absolute net account return less an explicitly
  supplied risk-free rate, never benchmark-relative return.

Real DSA daily data is intentionally not wired here.  Until a separate data
contract supplies unadjusted OHLC plus explicit dividend/split events, using
front-adjusted DSA prices is only a ``复权价格收益模拟`` and must not be
presented as an exact cash/share ledger or formal post-cost evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
import statistics
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import exchange_calendars as xcals
import pandas as pd


V0_1_ANNUAL_TRADING_DAYS = 242


@dataclass(frozen=True)
class DailyBar:
    """One unadjusted, completed A-share daily OHLCV bar."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class DailyTargetWeight:
    """A weight known only after ``signal_date`` closes."""

    signal_date: date
    target_weight: float


@dataclass(frozen=True)
class PortfolioDailyTargetWeights:
    """Portfolio weights known only after one shared signal-date close."""

    signal_date: date
    target_weights: Mapping[str, float]


@dataclass(frozen=True)
class BacktestAssumptions:
    """Explicit account, cost, and metric assumptions for one run.

    ``annual_risk_free_rate`` intentionally has no default: every experiment
    must record the selected one-year government-bond rate, observation date,
    and source with the calculation assumptions.
    """

    initial_cash: float
    commission_rate: float
    minimum_commission: float
    stamp_duty_rate: float
    transfer_fee_rate: float
    slippage_rate: float
    annual_trading_days: int
    annual_risk_free_rate: float
    risk_free_rate_source: str
    risk_free_rate_as_of: date
    lot_size: int = 100


@dataclass(frozen=True)
class TradeRecord:
    """A complete virtual fill, including every configured cost component."""

    signal_date: date
    execution_date: date
    symbol: str
    side: str
    target_weight: float
    reference_price: float
    execution_price: float
    requested_quantity: int
    quantity: int
    gross_amount: float
    commission: float
    stamp_duty: float
    transfer_fee: float
    total_fees: float
    cash_before: float
    cash_after: float
    shares_before: int
    shares_after: int
    status: str = "filled"
    reason: Optional[str] = None


@dataclass(frozen=True)
class DailyNavRecord:
    """End-of-session account state marked using that session's close."""

    date: date
    cash: float
    shares: int
    close_price: float
    market_value: float
    account_value: float
    nav: float
    daily_return: Optional[float]


@dataclass(frozen=True)
class PortfolioDailyNavRecord:
    """End-of-session shared-cash portfolio state."""

    date: date
    cash: float
    positions: Mapping[str, int]
    market_value: float
    account_value: float
    nav: float
    daily_return: Optional[float]


@dataclass(frozen=True)
class BacktestMetrics:
    """Cost-after, absolute-return performance metrics."""

    total_return: float
    sharpe_ratio: Optional[float]
    max_drawdown: float


@dataclass(frozen=True)
class BacktestPath:
    """One strategy or benchmark execution path."""

    trades: List[TradeRecord]
    daily_nav: List[DailyNavRecord]
    metrics: BacktestMetrics


@dataclass(frozen=True)
class TargetPositionBacktestResult(BacktestPath):
    """Strategy path with a same-accounting buy-and-hold comparison path."""

    benchmark: BacktestPath
    assumptions: BacktestAssumptions


@dataclass(frozen=True)
class PortfolioTargetPositionBacktestResult:
    """Auditable shared-cash portfolio execution result."""

    trades: List[TradeRecord]
    daily_nav: List[PortfolioDailyNavRecord]
    metrics: BacktestMetrics
    assumptions: BacktestAssumptions


def run_target_position_backtest(
    *,
    symbol: str,
    bars: Sequence[DailyBar],
    target_weights: Sequence[DailyTargetWeight],
    assumptions: BacktestAssumptions,
) -> TargetPositionBacktestResult:
    """Backtest frozen target weights and a same-cost buy-and-hold benchmark."""

    ordered_bars = _validate_inputs(bars=bars, target_weights=target_weights, assumptions=assumptions)
    strategy = _run_path(
        symbol=symbol,
        bars=ordered_bars,
        target_weights=target_weights,
        assumptions=assumptions,
    )
    first_signal_date = min(target.signal_date for target in target_weights)
    benchmark = _run_path(
        symbol=symbol,
        bars=ordered_bars,
        target_weights=[DailyTargetWeight(signal_date=first_signal_date, target_weight=1.0)],
        assumptions=assumptions,
    )
    return TargetPositionBacktestResult(
        trades=strategy.trades,
        daily_nav=strategy.daily_nav,
        metrics=strategy.metrics,
        benchmark=benchmark,
        assumptions=assumptions,
    )


def run_portfolio_target_position_backtest(
    *,
    bars_by_symbol: Mapping[str, Sequence[DailyBar]],
    target_weights: Sequence[PortfolioDailyTargetWeights],
    assumptions: BacktestAssumptions,
) -> PortfolioTargetPositionBacktestResult:
    """Simulate frozen same-day portfolio targets with one shared cash ledger.

    This is additive to the single-symbol v0.1 engine.  Every rebalance is
    scheduled for the following XSHG session; reductions execute before buys
    so released cash is available to the same opening batch.
    """

    if not bars_by_symbol:
        raise ValueError("bars_by_symbol must not be empty")
    if not target_weights:
        raise ValueError("target_weights must not be empty")
    symbols = tuple(sorted(bars_by_symbol))
    if any(not symbol.strip() for symbol in symbols):
        raise ValueError("symbols must not be blank")

    ordered_bars: Dict[str, List[DailyBar]] = {}
    target_dates = [target.signal_date for target in target_weights]
    if len(set(target_dates)) != len(target_dates):
        raise ValueError("target_weights must not contain duplicate signal dates")
    for target in target_weights:
        if set(target.target_weights) != set(symbols):
            raise ValueError("each portfolio target must contain exactly the portfolio symbols")
        if sum(target.target_weights.values()) > 1.0 + 1e-9:
            raise ValueError("portfolio target weights must not exceed 1.0 in total")
        for weight in target.target_weights.values():
            if not math.isfinite(weight) or not 0.0 <= weight <= 1.0:
                raise ValueError("portfolio target weights must be finite ratios from 0 to 1")
    for symbol in symbols:
        ordered_bars[symbol] = _validate_inputs(
            bars=bars_by_symbol[symbol],
            target_weights=[
                DailyTargetWeight(signal_date=item.signal_date, target_weight=item.target_weights[symbol])
                for item in target_weights
            ],
            assumptions=assumptions,
        )

    bars_by_date = {
        symbol: {bar.date: bar for bar in bars}
        for symbol, bars in ordered_bars.items()
    }
    common_dates = sorted(set.intersection(*(set(rows) for rows in bars_by_date.values())))
    calendar = xcals.get_calendar("XSHG")
    targets_by_execution_date: Dict[date, PortfolioDailyTargetWeights] = {}
    for target in target_weights:
        execution_date = calendar.next_session(pd.Timestamp(target.signal_date)).date()
        if execution_date not in common_dates:
            raise ValueError(
                f"no completed shared bar for next trading session {execution_date} after signal {target.signal_date}"
            )
        targets_by_execution_date[execution_date] = target

    cash = assumptions.initial_cash
    positions = {symbol: 0 for symbol in symbols}
    trades: List[TradeRecord] = []
    nav_records: List[PortfolioDailyNavRecord] = []
    previous_account_value: Optional[float] = None

    for session_date in common_dates:
        target = targets_by_execution_date.get(session_date)
        if target is not None:
            open_equity = cash + sum(
                positions[symbol] * bars_by_date[symbol][session_date].open
                for symbol in symbols
            )
            desired_positions = {
                symbol: (int((open_equity * target.target_weights[symbol]) // bars_by_date[symbol][session_date].open)
                         // assumptions.lot_size) * assumptions.lot_size
                for symbol in symbols
            }
            for symbol in symbols:
                if desired_positions[symbol] < positions[symbol]:
                    trade = _execute_desired_shares(
                        symbol=symbol,
                        signal_date=target.signal_date,
                        execution_date=session_date,
                        target_weight=target.target_weights[symbol],
                        reference_price=bars_by_date[symbol][session_date].open,
                        desired_shares=desired_positions[symbol],
                        cash=cash,
                        shares=positions[symbol],
                        assumptions=assumptions,
                    )
                    cash, positions[symbol] = trade.cash_after, trade.shares_after
                    trades.append(trade)
            for symbol in symbols:
                if desired_positions[symbol] >= positions[symbol]:
                    trade = _execute_desired_shares(
                        symbol=symbol,
                        signal_date=target.signal_date,
                        execution_date=session_date,
                        target_weight=target.target_weights[symbol],
                        reference_price=bars_by_date[symbol][session_date].open,
                        desired_shares=desired_positions[symbol],
                        cash=cash,
                        shares=positions[symbol],
                        assumptions=assumptions,
                    )
                    cash, positions[symbol] = trade.cash_after, trade.shares_after
                    trades.append(trade)

        market_value = sum(
            positions[symbol] * bars_by_date[symbol][session_date].close
            for symbol in symbols
        )
        account_value = cash + market_value
        daily_return = None if previous_account_value is None else account_value / previous_account_value - 1.0
        nav_records.append(
            PortfolioDailyNavRecord(
                date=session_date,
                cash=cash,
                positions=dict(positions),
                market_value=market_value,
                account_value=account_value,
                nav=account_value / assumptions.initial_cash,
                daily_return=daily_return,
            )
        )
        previous_account_value = account_value

    return PortfolioTargetPositionBacktestResult(
        trades=trades,
        daily_nav=nav_records,
        metrics=_calculate_metrics(nav_records, assumptions),
        assumptions=assumptions,
    )


def _validate_inputs(
    *,
    bars: Sequence[DailyBar],
    target_weights: Sequence[DailyTargetWeight],
    assumptions: BacktestAssumptions,
) -> List[DailyBar]:
    if not bars:
        raise ValueError("bars must not be empty")
    if not target_weights:
        raise ValueError("target_weights must not be empty")
    if assumptions.initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if assumptions.lot_size <= 0:
        raise ValueError("lot_size must be positive")
    if assumptions.annual_trading_days <= 0:
        raise ValueError("annual_trading_days must be positive")
    for field_name in ("commission_rate", "minimum_commission", "stamp_duty_rate", "transfer_fee_rate", "slippage_rate"):
        if getattr(assumptions, field_name) < 0:
            raise ValueError(f"{field_name} must be non-negative")
    if not math.isfinite(assumptions.annual_risk_free_rate):
        raise ValueError("annual_risk_free_rate must be finite")
    if not assumptions.risk_free_rate_source.strip():
        raise ValueError("risk_free_rate_source must not be blank")

    ordered_bars = sorted(bars, key=lambda bar: bar.date)
    if len({bar.date for bar in ordered_bars}) != len(ordered_bars):
        raise ValueError("bars must not contain duplicate dates")
    if list(bars) != ordered_bars:
        raise ValueError("bars must be supplied in ascending date order")
    calendar = xcals.get_calendar("XSHG")
    for bar in ordered_bars:
        if not calendar.is_session(pd.Timestamp(bar.date)):
            raise ValueError(f"bar date {bar.date} is not an XSHG trading session")
        for price in (bar.open, bar.high, bar.low, bar.close):
            if not math.isfinite(price) or price <= 0:
                raise ValueError(f"bar prices must be positive and finite on {bar.date}")
        if not math.isfinite(bar.volume) or bar.volume <= 0:
            raise ValueError(f"bar volume must be positive and finite on {bar.date}")
    signal_dates = [target.signal_date for target in target_weights]
    if len(set(signal_dates)) != len(signal_dates):
        raise ValueError("target_weights must not contain duplicate signal dates")
    bar_dates = {bar.date for bar in ordered_bars}
    for target in target_weights:
        if target.signal_date not in bar_dates:
            raise ValueError(f"signal date {target.signal_date} is absent from bars")
        if not math.isfinite(target.target_weight) or not 0.0 <= target.target_weight <= 1.0:
            raise ValueError("target_weight must be a finite ratio from 0 to 1")
    return ordered_bars


def _run_path(
    *,
    symbol: str,
    bars: Sequence[DailyBar],
    target_weights: Sequence[DailyTargetWeight],
    assumptions: BacktestAssumptions,
) -> BacktestPath:
    bar_by_date = {bar.date: bar for bar in bars}
    calendar = xcals.get_calendar("XSHG")
    orders_by_execution_date: Dict[date, DailyTargetWeight] = {}
    for target in target_weights:
        next_session = calendar.next_session(pd.Timestamp(target.signal_date)).date()
        if next_session not in bar_by_date:
            raise ValueError(
                f"no completed bar for next trading session {next_session} after signal {target.signal_date}"
            )
        orders_by_execution_date[next_session] = target

    cash = assumptions.initial_cash
    shares = 0
    trades: List[TradeRecord] = []
    nav_records: List[DailyNavRecord] = []
    previous_account_value: Optional[float] = None

    for bar in bars:
        target = orders_by_execution_date.get(bar.date)
        if target is not None:
            trade = _rebalance_at_open(
                symbol=symbol,
                signal_date=target.signal_date,
                execution_date=bar.date,
                target_weight=target.target_weight,
                reference_price=bar.open,
                cash=cash,
                shares=shares,
                assumptions=assumptions,
            )
            cash = trade.cash_after
            shares = trade.shares_after
            # A rejected/unfilled order is part of the audit trail too; hiding
            # it would make cash-only paths look as if no decision was made.
            trades.append(trade)

        market_value = shares * bar.close
        account_value = cash + market_value
        daily_return = None if previous_account_value is None else (account_value / previous_account_value) - 1.0
        nav_records.append(
            DailyNavRecord(
                date=bar.date,
                cash=cash,
                shares=shares,
                close_price=bar.close,
                market_value=market_value,
                account_value=account_value,
                nav=account_value / assumptions.initial_cash,
                daily_return=daily_return,
            )
        )
        previous_account_value = account_value

    return BacktestPath(trades=trades, daily_nav=nav_records, metrics=_calculate_metrics(nav_records, assumptions))


def _rebalance_at_open(
    *,
    symbol: str,
    signal_date: date,
    execution_date: date,
    target_weight: float,
    reference_price: float,
    cash: float,
    shares: int,
    assumptions: BacktestAssumptions,
) -> TradeRecord:
    pre_trade_equity = cash + shares * reference_price
    desired_shares = int((pre_trade_equity * target_weight) // reference_price)
    desired_shares = (desired_shares // assumptions.lot_size) * assumptions.lot_size
    return _execute_desired_shares(
        symbol=symbol,
        signal_date=signal_date,
        execution_date=execution_date,
        target_weight=target_weight,
        reference_price=reference_price,
        desired_shares=desired_shares,
        cash=cash,
        shares=shares,
        assumptions=assumptions,
    )


def _execute_desired_shares(
    *,
    symbol: str,
    signal_date: date,
    execution_date: date,
    target_weight: float,
    reference_price: float,
    desired_shares: int,
    cash: float,
    shares: int,
    assumptions: BacktestAssumptions,
) -> TradeRecord:
    """Execute one desired share count using the v0.1 cost and lot arithmetic."""

    requested_quantity = abs(desired_shares - shares)
    if desired_shares == shares:
        return _no_trade_record(
            symbol=symbol,
            signal_date=signal_date,
            execution_date=execution_date,
            target_weight=target_weight,
            reference_price=reference_price,
            cash=cash,
            shares=shares,
        )

    side = "buy" if desired_shares > shares else "sell"
    execution_price = reference_price * (1.0 + assumptions.slippage_rate if side == "buy" else 1.0 - assumptions.slippage_rate)
    quantity = requested_quantity
    if side == "buy":
        while quantity > 0 and _buy_cash_required(quantity, execution_price, assumptions) > cash + 1e-9:
            quantity -= assumptions.lot_size
        if quantity <= 0:
            return _no_trade_record(
                symbol=symbol,
                signal_date=signal_date,
                execution_date=execution_date,
                target_weight=target_weight,
                reference_price=reference_price,
                cash=cash,
                shares=shares,
                reason="insufficient_cash_after_costs",
            )

    gross_amount = quantity * execution_price
    commission = max(gross_amount * assumptions.commission_rate, assumptions.minimum_commission)
    transfer_fee = gross_amount * assumptions.transfer_fee_rate
    stamp_duty = gross_amount * assumptions.stamp_duty_rate if side == "sell" else 0.0
    total_fees = commission + transfer_fee + stamp_duty
    cash_after = cash - gross_amount - total_fees if side == "buy" else cash + gross_amount - total_fees
    shares_after = shares + quantity if side == "buy" else shares - quantity
    return TradeRecord(
        signal_date=signal_date,
        execution_date=execution_date,
        symbol=symbol,
        side=side,
        target_weight=target_weight,
        reference_price=reference_price,
        execution_price=execution_price,
        requested_quantity=requested_quantity,
        quantity=quantity,
        gross_amount=gross_amount,
        commission=commission,
        stamp_duty=stamp_duty,
        transfer_fee=transfer_fee,
        total_fees=total_fees,
        cash_before=cash,
        cash_after=cash_after,
        shares_before=shares,
        shares_after=shares_after,
    )


def _buy_cash_required(quantity: int, execution_price: float, assumptions: BacktestAssumptions) -> float:
    gross_amount = quantity * execution_price
    commission = max(gross_amount * assumptions.commission_rate, assumptions.minimum_commission)
    return gross_amount + commission + gross_amount * assumptions.transfer_fee_rate


def _no_trade_record(
    *,
    symbol: str,
    signal_date: date,
    execution_date: date,
    target_weight: float,
    reference_price: float,
    cash: float,
    shares: int,
    reason: str = "target_already_met",
) -> TradeRecord:
    return TradeRecord(
        signal_date=signal_date,
        execution_date=execution_date,
        symbol=symbol,
        side="none",
        target_weight=target_weight,
        reference_price=reference_price,
        execution_price=reference_price,
        requested_quantity=0,
        quantity=0,
        gross_amount=0.0,
        commission=0.0,
        stamp_duty=0.0,
        transfer_fee=0.0,
        total_fees=0.0,
        cash_before=cash,
        cash_after=cash,
        shares_before=shares,
        shares_after=shares,
        status="not_filled",
        reason=reason,
    )


def _calculate_metrics(records: Iterable[DailyNavRecord], assumptions: BacktestAssumptions) -> BacktestMetrics:
    nav_records = list(records)
    navs = [record.nav for record in nav_records]
    peak = navs[0]
    max_drawdown = 0.0
    for nav in navs:
        peak = max(peak, nav)
        max_drawdown = min(max_drawdown, nav / peak - 1.0)
    daily_returns = [record.daily_return for record in nav_records if record.daily_return is not None]
    daily_risk_free = (1.0 + assumptions.annual_risk_free_rate) ** (1.0 / assumptions.annual_trading_days) - 1.0
    excess_returns = [daily_return - daily_risk_free for daily_return in daily_returns]
    if len(excess_returns) < 2:
        sharpe_ratio = None
    else:
        volatility = statistics.stdev(excess_returns)
        sharpe_ratio = None if volatility == 0 else math.sqrt(assumptions.annual_trading_days) * statistics.mean(excess_returns) / volatility
    return BacktestMetrics(
        total_return=navs[-1] - 1.0,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
    )
