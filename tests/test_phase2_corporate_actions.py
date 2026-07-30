from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from research.prototype.phase2.corporate_actions import (
    AdjustmentFactorRow,
    ResolvedCorporateAction,
    apply_adjustment_factors,
    build_adjustment_factor_rows,
    visible_actions_as_of,
)
from research.prototype.pit_replay.models import PriceObservation


def _fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "action_id": "action-001",
        "symbol": "600000.XSHG",
        "action_type": "cash_dividend",
        "announced_at": "2024-01-02T09:00:00+08:00",
        "ex_date": date(2024, 1, 3),
        "payment_date": date(2024, 1, 5),
        "cash_per_share": 0.5,
        "share_ratio": None,
        "price_adjustment_ratio": 0.95,
        "currency": "CNY",
        "evidence_ids": ("evidence-001",),
        "resolver_version": "phase2-v1",
        "input_sha256": "a" * 64,
        "pit_status": "strict_pit",
    }
    return fields | overrides


def _cash_dividend(**overrides: object) -> ResolvedCorporateAction:
    return ResolvedCorporateAction(**_fields(**overrides))


def _share_ratio(**overrides: object) -> ResolvedCorporateAction:
    return ResolvedCorporateAction(
        **_fields(
            **{
                "action_type": "share_ratio_adjustment",
                "payment_date": None,
                "cash_per_share": None,
                "share_ratio": 2.0,
                "price_adjustment_ratio": None,
            }
            | overrides
        )
    )


def _prices() -> tuple[PriceObservation, ...]:
    return (
        PriceObservation("AAA", date(2024, 1, 2), 10.0, 11.0, 9.0, 10.0, 100.0),
        PriceObservation("AAA", date(2024, 1, 3), 8.0, 9.0, 7.0, 8.0, 120.0),
    )


def _factors_with_two_for_one_split() -> tuple[AdjustmentFactorRow, ...]:
    return (
        AdjustmentFactorRow(
            "AAA", date(2024, 1, 2), 1.0, (), "phase2-factor-v1", "b" * 64, "strict_pit"
        ),
        AdjustmentFactorRow(
            "AAA", date(2024, 1, 3), 2.0, ("split",), "phase2-factor-v1", "b" * 64, "strict_pit"
        ),
    )


def test_factor_rows_do_not_change_when_a_future_action_is_removed() -> None:
    sessions = (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4))
    early = _share_ratio(symbol="AAA", ex_date=date(2024, 1, 3), share_ratio=2.0)
    future = _share_ratio(
        symbol="AAA", action_id="future", ex_date=date(2024, 1, 4), share_ratio=1.5
    )

    complete = build_adjustment_factor_rows(
        symbol="AAA",
        sessions=sessions,
        actions=(early, future),
        decision_cutoff_time=time(15, 30),
        timezone_name="Asia/Shanghai",
    )
    truncated = build_adjustment_factor_rows(
        symbol="AAA",
        sessions=sessions[:2],
        actions=(early,),
        decision_cutoff_time=time(15, 30),
        timezone_name="Asia/Shanghai",
    )

    assert complete[:2] == truncated


def test_factor_projection_rejects_duplicate_action_ids_before_calculating_rows() -> None:
    first = _share_ratio(symbol="AAA", action_id="duplicated", share_ratio=2.0)
    conflicting_duplicate = _share_ratio(symbol="AAA", action_id="duplicated", share_ratio=1.5)

    with pytest.raises(ValueError, match="duplicate action_id"):
        build_adjustment_factor_rows(
            symbol="AAA",
            sessions=(date(2024, 1, 3),),
            actions=(first, conflicting_duplicate),
            decision_cutoff_time=time(15, 30),
            timezone_name="Asia/Shanghai",
        )


def test_share_adjustment_projects_only_prices_and_preserves_volume() -> None:
    adjusted = apply_adjustment_factors(
        observations=_prices(), factors=_factors_with_two_for_one_split()
    )

    assert adjusted[-1].open == pytest.approx(_prices()[-1].open * 2.0)
    assert adjusted[-1].high == pytest.approx(_prices()[-1].high * 2.0)
    assert adjusted[-1].low == pytest.approx(_prices()[-1].low * 2.0)
    assert adjusted[-1].close == pytest.approx(_prices()[-1].close * 2.0)
    assert adjusted[-1].volume == _prices()[-1].volume


def test_projection_rejects_missing_factor_or_dividend_without_price_evidence() -> None:
    with pytest.raises(ValueError, match="cash dividend requires explicit price adjustment evidence"):
        build_adjustment_factor_rows(
            symbol="AAA",
            sessions=(date(2024, 1, 3),),
            actions=(
                _cash_dividend(
                    symbol="AAA", ex_date=date(2024, 1, 3), price_adjustment_ratio=None
                ),
            ),
            decision_cutoff_time=time(15, 30),
            timezone_name="Asia/Shanghai",
        )

    with pytest.raises(ValueError, match="missing adjustment factor"):
        apply_adjustment_factors(observations=_prices(), factors=_factors_with_two_for_one_split()[:1])


def test_visible_actions_excludes_after_cutoff_announcement() -> None:
    action = _cash_dividend(
        announced_at="2024-01-03T16:00:00+08:00", ex_date=date(2024, 1, 3)
    )

    visible = visible_actions_as_of(
        (action,),
        as_of=date(2024, 1, 3),
        decision_cutoff=datetime(2024, 1, 3, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert visible == ()


def test_resolved_action_rejects_unsupported_rights_issue() -> None:
    with pytest.raises(ValueError, match="unsupported corporate action type"):
        ResolvedCorporateAction(**_fields(action_type="rights_issue"))


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"announced_at": "2024-01-02T09:00:00"}, "announced_at must include timezone"),
        ({"pit_status": "provider_historical_snapshot"}, "strict_pit"),
        ({"action_type": "cash_dividend", "payment_date": None}, "payment_date"),
        ({"action_type": "share_ratio_adjustment", "share_ratio": 0.0}, "share_ratio"),
    ],
)
def test_resolved_action_rejects_non_strict_or_ambiguous_inputs(
    fields: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ResolvedCorporateAction(**(_fields() | fields))


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"cash_per_share": float("nan")}, "cash_per_share"),
        ({"price_adjustment_ratio": 0.0}, "price_adjustment_ratio"),
        ({"payment_date": date(2024, 1, 2)}, "payment_date"),
        ({"evidence_ids": ("",)}, "evidence_ids"),
        ({"share_ratio": 1.2}, "share_ratio"),
        ({"share_ratio": float("nan")}, "share_ratio"),
    ],
)
def test_cash_dividend_rejects_invalid_contract_fields(
    fields: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ResolvedCorporateAction(**(_fields() | fields))


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"payment_date": date(2024, 1, 5)}, "payment_date"),
        ({"cash_per_share": 0.5}, "cash_per_share"),
        ({"share_ratio": None}, "share_ratio"),
    ],
)
def test_share_ratio_adjustment_rejects_non_share_fields(
    fields: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ResolvedCorporateAction(
            **(
                _fields()
                | {
                    "action_type": "share_ratio_adjustment",
                    "payment_date": None,
                    "cash_per_share": None,
                    "share_ratio": 1.2,
                }
                | fields
            )
        )


def test_share_ratio_adjustment_accepts_fractional_mathematical_ratio() -> None:
    action = ResolvedCorporateAction(
        **_fields(
            action_type="share_ratio_adjustment",
            payment_date=None,
            cash_per_share=None,
            share_ratio=1.2,
        )
    )

    assert action.share_ratio == 1.2


def test_visible_actions_filters_future_ex_date_and_orders_deterministically() -> None:
    later_id = _cash_dividend(action_id="z-action", ex_date=date(2024, 1, 3))
    earlier_id = _cash_dividend(action_id="a-action", ex_date=date(2024, 1, 3))
    future_ex_date = _cash_dividend(action_id="future", ex_date=date(2024, 1, 4))

    visible = visible_actions_as_of(
        (later_id, future_ex_date, earlier_id),
        as_of=date(2024, 1, 3),
        decision_cutoff=datetime(2024, 1, 3, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert tuple(action.action_id for action in visible) == ("a-action", "z-action")


def test_visible_actions_rejects_naive_decision_cutoff() -> None:
    with pytest.raises(ValueError, match="decision_cutoff must include timezone"):
        visible_actions_as_of(
            (),
            as_of=date(2024, 1, 3),
            decision_cutoff=datetime(2024, 1, 3, 15, 30),
        )
