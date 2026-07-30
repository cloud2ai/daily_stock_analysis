"""Tests for the deterministic target-position backtest engine.

All fixtures use artificial, non-adjusted prices and contain no corporate
actions.  They deliberately exercise only the engine's pure-accounting layer.
"""

from datetime import date
import math
import statistics

import pytest

import src.core.target_position_backtest as target_backtest

from src.core.target_position_backtest import (
    BacktestAssumptions,
    DailyBar,
    DailyTargetWeight,
    run_target_position_backtest,
)


def test_portfolio_rebalances_shared_cash_at_next_open() -> None:
    assumptions = BacktestAssumptions(
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
    bars = {
        "AAA": [
            DailyBar(date(2024, 1, 2), 10.0, 10.0, 10.0, 10.0, 1_000),
            DailyBar(date(2024, 1, 3), 10.0, 10.0, 10.0, 10.0, 1_000),
            DailyBar(date(2024, 1, 4), 10.0, 10.0, 10.0, 10.0, 1_000),
        ],
        "BBB": [
            DailyBar(date(2024, 1, 2), 10.0, 10.0, 10.0, 10.0, 1_000),
            DailyBar(date(2024, 1, 3), 10.0, 10.0, 10.0, 10.0, 1_000),
            DailyBar(date(2024, 1, 4), 10.0, 10.0, 10.0, 10.0, 1_000),
        ],
    }

    result = target_backtest.run_portfolio_target_position_backtest(
        bars_by_symbol=bars,
        target_weights=[
            target_backtest.PortfolioDailyTargetWeights(date(2024, 1, 2), {"AAA": 1.0, "BBB": 0.0}),
            target_backtest.PortfolioDailyTargetWeights(date(2024, 1, 3), {"AAA": 0.0, "BBB": 1.0}),
        ],
        assumptions=assumptions,
    )

    executed = [
        (trade.execution_date, trade.symbol, trade.side, trade.quantity)
        for trade in result.trades
        if trade.status == "filled"
    ]
    assert executed == [
        (date(2024, 1, 3), "AAA", "buy", 1_000),
        (date(2024, 1, 4), "AAA", "sell", 1_000),
        (date(2024, 1, 4), "BBB", "buy", 1_000),
    ]
    assert result.daily_nav[-1].cash == pytest.approx(0.0)
    assert result.daily_nav[-1].positions == {"AAA": 0, "BBB": 1_000}


def test_buy_and_hold_uses_next_session_open_and_marks_nav_at_close() -> None:
    result = run_target_position_backtest(
        symbol="TEST",
        bars=[
            DailyBar(date(2024, 1, 2), open=10.0, high=10.0, low=10.0, close=10.0, volume=1_000),
            DailyBar(date(2024, 1, 3), open=10.0, high=11.0, low=10.0, close=11.0, volume=1_000),
            DailyBar(date(2024, 1, 4), open=11.0, high=12.0, low=11.0, close=12.0, volume=1_000),
            DailyBar(date(2024, 1, 5), open=12.0, high=12.0, low=9.0, close=9.0, volume=1_000),
            DailyBar(date(2024, 1, 8), open=9.0, high=10.0, low=9.0, close=10.0, volume=1_000),
        ],
        target_weights=[DailyTargetWeight(signal_date=date(2024, 1, 2), target_weight=1.0)],
        assumptions=BacktestAssumptions(
            initial_cash=10_000.0,
            commission_rate=0.001,
            minimum_commission=0.0,
            stamp_duty_rate=0.005,
            transfer_fee_rate=0.001,
            slippage_rate=0.01,
            annual_trading_days=242,
            annual_risk_free_rate=0.0,
            risk_free_rate_source="deterministic test fixture",
            risk_free_rate_as_of=date(2024, 1, 2),
        ),
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.signal_date == date(2024, 1, 2)
    assert trade.execution_date == date(2024, 1, 3)
    assert trade.side == "buy"
    assert trade.reference_price == pytest.approx(10.0)
    assert trade.execution_price == pytest.approx(10.1)
    assert trade.quantity == 900
    assert trade.commission == pytest.approx(9.09)
    assert trade.stamp_duty == pytest.approx(0.0)
    assert trade.transfer_fee == pytest.approx(9.09)
    assert trade.cash_after == pytest.approx(891.82)

    assert [record.account_value for record in result.daily_nav] == pytest.approx(
        [10_000.0, 10_791.82, 11_691.82, 8_991.82, 9_891.82]
    )
    assert [record.nav for record in result.daily_nav] == pytest.approx(
        [1.0, 1.079182, 1.169182, 0.899182, 0.989182]
    )
    assert result.metrics.max_drawdown == pytest.approx((8_991.82 / 11_691.82) - 1)
    assert result.benchmark.daily_nav == result.daily_nav
    assert result.benchmark.metrics == result.metrics


def test_rebalance_to_cash_charges_sell_stamp_duty_and_uses_absolute_return_sharpe() -> None:
    assumptions = BacktestAssumptions(
        initial_cash=10_000.0,
        commission_rate=0.001,
        minimum_commission=0.0,
        stamp_duty_rate=0.005,
        transfer_fee_rate=0.001,
        slippage_rate=0.01,
        annual_trading_days=242,
        annual_risk_free_rate=0.0,
        risk_free_rate_source="deterministic test fixture",
        risk_free_rate_as_of=date(2024, 1, 2),
    )
    result = run_target_position_backtest(
        symbol="TEST",
        bars=[
            DailyBar(date(2024, 1, 2), open=10.0, high=10.0, low=10.0, close=10.0, volume=1_000),
            DailyBar(date(2024, 1, 3), open=10.0, high=10.0, low=10.0, close=10.0, volume=1_000),
            DailyBar(date(2024, 1, 4), open=12.0, high=12.0, low=12.0, close=12.0, volume=1_000),
            DailyBar(date(2024, 1, 5), open=12.0, high=12.0, low=12.0, close=12.0, volume=1_000),
        ],
        target_weights=[
            DailyTargetWeight(signal_date=date(2024, 1, 2), target_weight=1.0),
            DailyTargetWeight(signal_date=date(2024, 1, 3), target_weight=0.0),
        ],
        assumptions=assumptions,
    )

    sell = result.trades[1]
    assert sell.execution_date == date(2024, 1, 4)
    assert sell.side == "sell"
    assert sell.execution_price == pytest.approx(11.88)
    assert sell.quantity == 900
    assert sell.gross_amount == pytest.approx(10_692.0)
    assert sell.commission == pytest.approx(10.692)
    assert sell.transfer_fee == pytest.approx(10.692)
    assert sell.stamp_duty == pytest.approx(53.46)
    assert sell.total_fees == pytest.approx(74.844)
    assert sell.cash_after == pytest.approx(11_508.976)
    assert sell.shares_after == 0

    account_values = [record.account_value for record in result.daily_nav]
    daily_returns = [
        account_values[index] / account_values[index - 1] - 1.0
        for index in range(1, len(account_values))
    ]
    expected_sharpe = math.sqrt(242) * statistics.mean(daily_returns) / statistics.stdev(daily_returns)
    assert result.metrics.sharpe_ratio == pytest.approx(expected_sharpe)
    assert result.metrics.max_drawdown == pytest.approx((9_891.82 / 10_000.0) - 1.0)
    assert result.benchmark.metrics.total_return != result.metrics.total_return


def test_friday_signal_executes_on_the_next_monday_session() -> None:
    result = run_target_position_backtest(
        symbol="TEST",
        bars=[
            DailyBar(date(2024, 1, 5), open=10.0, high=10.0, low=10.0, close=10.0, volume=1_000),
            DailyBar(date(2024, 1, 8), open=10.0, high=10.0, low=10.0, close=10.0, volume=1_000),
        ],
        target_weights=[DailyTargetWeight(signal_date=date(2024, 1, 5), target_weight=1.0)],
        assumptions=BacktestAssumptions(
            initial_cash=10_000.0,
            commission_rate=0.0,
            minimum_commission=0.0,
            stamp_duty_rate=0.0,
            transfer_fee_rate=0.0,
            slippage_rate=0.0,
            annual_trading_days=242,
            annual_risk_free_rate=0.0,
            risk_free_rate_source="deterministic test fixture",
            risk_free_rate_as_of=date(2024, 1, 5),
        ),
    )

    assert result.trades[0].execution_date == date(2024, 1, 8)


def test_unaffordable_order_is_retained_as_an_auditable_unfilled_trade() -> None:
    result = run_target_position_backtest(
        symbol="TEST",
        bars=[
            DailyBar(date(2024, 1, 2), open=10.0, high=10.0, low=10.0, close=10.0, volume=1_000),
            DailyBar(date(2024, 1, 3), open=10.0, high=10.0, low=10.0, close=10.0, volume=1_000),
        ],
        target_weights=[DailyTargetWeight(signal_date=date(2024, 1, 2), target_weight=1.0)],
        assumptions=BacktestAssumptions(
            initial_cash=1_000.0,
            commission_rate=0.0,
            minimum_commission=10.0,
            stamp_duty_rate=0.0,
            transfer_fee_rate=0.0,
            slippage_rate=0.01,
            annual_trading_days=242,
            annual_risk_free_rate=0.0,
            risk_free_rate_source="deterministic test fixture",
            risk_free_rate_as_of=date(2024, 1, 2),
        ),
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.status == "not_filled"
    assert trade.reason == "insufficient_cash_after_costs"
    assert trade.quantity == 0
    assert result.daily_nav[-1].account_value == pytest.approx(1_000.0)


def test_sharpe_subtracts_the_explicit_absolute_risk_free_rate() -> None:
    annual_risk_free_rate = 0.0242
    result = run_target_position_backtest(
        symbol="TEST",
        bars=[
            DailyBar(date(2024, 1, 2), open=10.0, high=10.0, low=10.0, close=10.0, volume=1_000),
            DailyBar(date(2024, 1, 3), open=10.0, high=11.0, low=10.0, close=11.0, volume=1_000),
            DailyBar(date(2024, 1, 4), open=11.0, high=11.0, low=9.0, close=9.0, volume=1_000),
            DailyBar(date(2024, 1, 5), open=9.0, high=10.0, low=9.0, close=10.0, volume=1_000),
        ],
        target_weights=[DailyTargetWeight(signal_date=date(2024, 1, 2), target_weight=1.0)],
        assumptions=BacktestAssumptions(
            initial_cash=10_000.0,
            commission_rate=0.0,
            minimum_commission=0.0,
            stamp_duty_rate=0.0,
            transfer_fee_rate=0.0,
            slippage_rate=0.0,
            annual_trading_days=242,
            annual_risk_free_rate=annual_risk_free_rate,
            risk_free_rate_source="deterministic test fixture",
            risk_free_rate_as_of=date(2024, 1, 2),
        ),
    )

    daily_returns = [record.daily_return for record in result.daily_nav if record.daily_return is not None]
    daily_risk_free = (1 + annual_risk_free_rate) ** (1 / 242) - 1
    excess_returns = [daily_return - daily_risk_free for daily_return in daily_returns]
    expected_sharpe = math.sqrt(242) * statistics.mean(excess_returns) / statistics.stdev(excess_returns)
    assert result.metrics.sharpe_ratio == pytest.approx(expected_sharpe)


def test_result_retains_the_explicit_risk_free_rate_audit_metadata() -> None:
    assumptions = BacktestAssumptions(
        initial_cash=10_000.0,
        commission_rate=0.0,
        minimum_commission=0.0,
        stamp_duty_rate=0.0,
        transfer_fee_rate=0.0,
        slippage_rate=0.0,
        annual_trading_days=242,
        annual_risk_free_rate=0.0114,
        risk_free_rate_source="ChinaBond 1Y treasury curve",
        risk_free_rate_as_of=date(2026, 7, 22),
    )
    result = run_target_position_backtest(
        symbol="TEST",
        bars=[
            DailyBar(date(2024, 1, 2), open=10.0, high=10.0, low=10.0, close=10.0, volume=1_000),
            DailyBar(date(2024, 1, 3), open=10.0, high=10.0, low=10.0, close=10.0, volume=1_000),
        ],
        target_weights=[DailyTargetWeight(signal_date=date(2024, 1, 2), target_weight=1.0)],
        assumptions=assumptions,
    )

    assert result.assumptions == assumptions
