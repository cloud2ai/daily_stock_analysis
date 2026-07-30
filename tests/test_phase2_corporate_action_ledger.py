from datetime import date

import pytest

from research.prototype.phase2.corporate_action_ledger import (
    apply_close_corporate_actions,
    apply_open_corporate_actions,
)
from research.prototype.phase2.corporate_actions import ResolvedCorporateAction


def _fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "action_id": "action-001",
        "symbol": "AAA",
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


def test_ex_date_open_applies_share_ratio_before_trading() -> None:
    applied = apply_open_corporate_actions(
        event_date=date(2024, 1, 3),
        symbol="AAA",
        shares=1_000,
        cash=100.0,
        dividend_receivable=0.0,
        actions=(_share_ratio(ex_date=date(2024, 1, 3), share_ratio=1.2),),
        record_date_positions={},
        receivables_by_action_id={},
        opened_action_ids=frozenset(),
    )

    assert applied.shares == 1_200
    assert applied.cash == 100.0
    assert applied.records[0].phase == "ex_date_open"


def test_dividend_is_receivable_on_ex_date_and_cash_only_after_payment_close() -> None:
    action = _cash_dividend(
        ex_date=date(2024, 1, 3), payment_date=date(2024, 1, 5), cash_per_share=0.5
    )
    opened = apply_open_corporate_actions(
        event_date=date(2024, 1, 3),
        symbol="AAA",
        shares=1_200,
        cash=0.0,
        dividend_receivable=0.0,
        actions=(action,),
        record_date_positions={action.action_id: 1_200},
        receivables_by_action_id={},
        opened_action_ids=frozenset(),
    )
    settled = apply_close_corporate_actions(
        event_date=date(2024, 1, 5),
        symbol="AAA",
        shares=opened.shares,
        cash=opened.cash,
        dividend_receivable=opened.dividend_receivable,
        actions=(action,),
        receivables_by_action_id=opened.receivables_by_action_id,
    )

    assert (opened.cash, opened.dividend_receivable) == (0.0, 600.0)
    assert (settled.cash, settled.dividend_receivable) == (600.0, 0.0)


def test_fractional_share_result_is_rejected_without_mutating_account() -> None:
    receivables = {"unrelated": 2.0}
    opened_action_ids = frozenset({"already-opened"})

    with pytest.raises(ValueError, match="non-integral share result"):
        apply_open_corporate_actions(
            event_date=date(2024, 1, 3),
            symbol="AAA",
            shares=101,
            cash=10.0,
            dividend_receivable=2.0,
            actions=(_share_ratio(ex_date=date(2024, 1, 3), share_ratio=1.25),),
            record_date_positions={},
            receivables_by_action_id=receivables,
            opened_action_ids=opened_action_ids,
        )

    assert receivables == {"unrelated": 2.0}
    assert opened_action_ids == frozenset({"already-opened"})


def test_replaying_same_dividend_action_id_returns_rejected_duplicate_record() -> None:
    action = _cash_dividend(
        ex_date=date(2024, 1, 3), payment_date=date(2024, 1, 5), cash_per_share=0.5
    )
    first = apply_open_corporate_actions(
        event_date=date(2024, 1, 3),
        symbol="AAA",
        shares=100,
        cash=0.0,
        dividend_receivable=0.0,
        actions=(action,),
        record_date_positions={action.action_id: 100},
        receivables_by_action_id={},
        opened_action_ids=frozenset(),
    )
    second = apply_open_corporate_actions(
        event_date=date(2024, 1, 3),
        symbol="AAA",
        shares=first.shares,
        cash=first.cash,
        dividend_receivable=first.dividend_receivable,
        actions=(action,),
        record_date_positions={action.action_id: 100},
        receivables_by_action_id=first.receivables_by_action_id,
        opened_action_ids=first.opened_action_ids,
    )

    assert second.records[-1].status == "rejected"
    assert second.records[-1].reason == "duplicate_action_id"
    assert second.dividend_receivable == first.dividend_receivable
    assert second.receivables_by_action_id == first.receivables_by_action_id


def test_payment_date_cannot_settle_an_unrecognized_receivable() -> None:
    action = _cash_dividend(
        ex_date=date(2024, 1, 3), payment_date=date(2024, 1, 5), cash_per_share=0.5
    )

    with pytest.raises(ValueError, match="unrecognized dividend receivable"):
        apply_close_corporate_actions(
            event_date=date(2024, 1, 5),
            symbol="AAA",
            shares=100,
            cash=0.0,
            dividend_receivable=0.0,
            actions=(action,),
            receivables_by_action_id={},
        )


def test_open_and_close_reject_receivable_mapping_that_does_not_sum_to_balance() -> None:
    payable = _cash_dividend(action_id="payable", payment_date=date(2024, 1, 5))
    inconsistent = {"payable": 50.0, "later": 8.0}

    with pytest.raises(ValueError, match="receivable mapping must sum to dividend receivable"):
        apply_open_corporate_actions(
            event_date=date(2024, 1, 3),
            symbol="AAA",
            shares=100,
            cash=0.0,
            dividend_receivable=60.0,
            actions=(),
            record_date_positions={},
            receivables_by_action_id=inconsistent,
            opened_action_ids=frozenset(),
        )

    with pytest.raises(ValueError, match="receivable mapping must sum to dividend receivable"):
        apply_close_corporate_actions(
            event_date=date(2024, 1, 5),
            symbol="AAA",
            shares=100,
            cash=0.0,
            dividend_receivable=60.0,
            actions=(payable,),
            receivables_by_action_id=inconsistent,
        )


def test_payment_close_settles_matching_receivable_even_when_action_was_opened() -> None:
    payable = _cash_dividend(action_id="payable", payment_date=date(2024, 1, 5))
    later = _cash_dividend(action_id="later", payment_date=date(2024, 1, 6))

    settled = apply_close_corporate_actions(
        event_date=date(2024, 1, 5),
        symbol="AAA",
        shares=100,
        cash=0.0,
        dividend_receivable=58.0,
        actions=(later, payable),
        receivables_by_action_id={"payable": 50.0, "later": 8.0},
        opened_action_ids=frozenset({"payable", "later"}),
    )

    assert settled.cash == 50.0
    assert settled.dividend_receivable == 8.0
    assert settled.receivables_by_action_id == {"later": 8.0}
    assert settled.opened_action_ids == frozenset({"payable", "later"})
    assert tuple(record.action_id for record in settled.records) == ("payable",)


def test_open_actions_are_recorded_in_action_id_order() -> None:
    later = _share_ratio(action_id="z-action", ex_date=date(2024, 1, 3), share_ratio=2.0)
    earlier = _share_ratio(action_id="a-action", ex_date=date(2024, 1, 3), share_ratio=3.0)

    applied = apply_open_corporate_actions(
        event_date=date(2024, 1, 3),
        symbol="AAA",
        shares=100,
        cash=0.0,
        dividend_receivable=0.0,
        actions=(later, earlier),
        record_date_positions={},
        receivables_by_action_id={},
        opened_action_ids=frozenset(),
    )

    assert tuple(record.action_id for record in applied.records) == ("a-action", "z-action")


def test_dividend_requires_a_non_negative_integer_record_date_position() -> None:
    action = _cash_dividend(ex_date=date(2024, 1, 3))

    with pytest.raises(ValueError, match="record date position"):
        apply_open_corporate_actions(
            event_date=date(2024, 1, 3),
            symbol="AAA",
            shares=100,
            cash=0.0,
            dividend_receivable=0.0,
            actions=(action,),
            record_date_positions={action.action_id: -1},
            receivables_by_action_id={},
            opened_action_ids=frozenset(),
        )
