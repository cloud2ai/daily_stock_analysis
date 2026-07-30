from datetime import date, time

from src.core.target_position_backtest import (
    BacktestAssumptions,
    DailyBar,
    VariableUniversePortfolioDailyTargetWeights,
    run_variable_universe_portfolio_target_position_backtest,
)
from research.prototype.phase2.corporate_actions import ResolvedCorporateAction


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


def _bars() -> dict[str, list[DailyBar]]:
    return {
        "AAA": [
            DailyBar(date(2024, 1, 2), 10.0, 10.0, 10.0, 10.0, 1_000.0),
            DailyBar(date(2024, 1, 3), 10.0, 10.0, 10.0, 10.0, 1_000.0),
            DailyBar(date(2024, 1, 4), 10.0, 10.0, 10.0, 10.0, 1_000.0),
        ]
    }


def _cash_dividend_and_share_ratio() -> tuple[ResolvedCorporateAction, ResolvedCorporateAction]:
    common = {
        "symbol": "AAA", "announced_at": "2024-01-02T09:00:00+08:00",
        "currency": "CNY", "evidence_ids": ("evidence",), "resolver_version": "phase2-v1",
        "input_sha256": "a" * 64, "pit_status": "strict_pit",
    }
    return (
        ResolvedCorporateAction(action_id="split", action_type="share_ratio_adjustment", ex_date=date(2024, 1, 4), payment_date=None, cash_per_share=None, share_ratio=2.0, price_adjustment_ratio=None, **common),
        ResolvedCorporateAction(action_id="dividend", action_type="cash_dividend", ex_date=date(2024, 1, 4), payment_date=date(2024, 1, 5), cash_per_share=0.5, share_ratio=None, price_adjustment_ratio=0.95, **common),
    )


def test_variable_universe_applies_optional_corporate_action_ledger_to_held_position() -> None:
    bars = _bars()
    bars["AAA"].extend([
        DailyBar(date(2024, 1, 5), 10.0, 10.0, 10.0, 10.0, 1_000.0),
    ])
    split, dividend = _cash_dividend_and_share_ratio()

    result = run_variable_universe_portfolio_target_position_backtest(
        bars_by_symbol=bars,
        target_weights=[VariableUniversePortfolioDailyTargetWeights(date(2024, 1, 2), {"AAA": 1.0}, frozenset({"AAA"}))],
        assumptions=_assumptions(),
        corporate_actions_by_symbol={"AAA": (split, dividend)},
    )

    assert result.daily_nav[2].positions == {"AAA": 2_000}
    assert result.daily_nav[2].dividend_receivable == 500.0
    assert result.daily_nav[3].cash == 500.0
    assert result.daily_nav[3].dividend_receivable == 0.0
    assert [
        (record.action_id, record.event_date, record.phase, record.status)
        for record in result.corporate_action_records
    ] == [
        ("dividend", date(2024, 1, 4), "ex_date_open", "applied"),
        ("split", date(2024, 1, 4), "ex_date_open", "applied"),
        ("dividend", date(2024, 1, 5), "payment_date_close", "applied"),
    ]


def test_variable_universe_does_not_apply_action_announced_after_cutoff() -> None:
    bars = _bars()
    split, _ = _cash_dividend_and_share_ratio()
    after_cutoff_split = ResolvedCorporateAction(
        action_id=split.action_id,
        symbol=split.symbol,
        action_type=split.action_type,
        announced_at="2024-01-04T16:00:00+08:00",
        ex_date=split.ex_date,
        payment_date=split.payment_date,
        cash_per_share=split.cash_per_share,
        share_ratio=split.share_ratio,
        price_adjustment_ratio=split.price_adjustment_ratio,
        currency=split.currency,
        evidence_ids=split.evidence_ids,
        resolver_version=split.resolver_version,
        input_sha256=split.input_sha256,
        pit_status=split.pit_status,
    )

    result = run_variable_universe_portfolio_target_position_backtest(
        bars_by_symbol=bars,
        target_weights=[VariableUniversePortfolioDailyTargetWeights(date(2024, 1, 2), {"AAA": 1.0}, frozenset({"AAA"}))],
        assumptions=_assumptions(),
        corporate_actions_by_symbol={"AAA": (after_cutoff_split,)},
        decision_cutoff_time=time(15, 0),
    )

    assert result.daily_nav[2].positions == {"AAA": 1_000}
    assert result.corporate_action_records == []


def test_variable_universe_sells_removed_holding_at_next_open() -> None:
    result = run_variable_universe_portfolio_target_position_backtest(
        bars_by_symbol=_bars(),
        target_weights=[
            VariableUniversePortfolioDailyTargetWeights(date(2024, 1, 2), {"AAA": 1.0}, frozenset({"AAA"})),
            VariableUniversePortfolioDailyTargetWeights(date(2024, 1, 3), {}, frozenset()),
        ],
        assumptions=_assumptions(),
    )

    assert [(trade.symbol, trade.side, trade.execution_date) for trade in result.trades if trade.status == "filled"] == [
        ("AAA", "buy", date(2024, 1, 3)),
        ("AAA", "sell", date(2024, 1, 4)),
    ]


def test_variable_universe_records_an_unfilled_order_when_next_open_has_zero_volume() -> None:
    bars = _bars()
    bars["AAA"][1] = DailyBar(date(2024, 1, 3), 10.0, 10.0, 10.0, 10.0, 0.0)

    result = run_variable_universe_portfolio_target_position_backtest(
        bars_by_symbol=bars,
        target_weights=[
            VariableUniversePortfolioDailyTargetWeights(date(2024, 1, 2), {"AAA": 1.0}, frozenset({"AAA"})),
        ],
        assumptions=_assumptions(),
    )

    assert [(trade.status, trade.reason, trade.quantity) for trade in result.trades] == [
        ("not_filled", "non_tradable_volume_zero", 0),
    ]
    assert result.daily_nav[1].positions == {"AAA": 0}
    assert result.daily_nav[1].cash == 10_000.0
