"""Pure account transitions for strict Phase 2 corporate actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isclose, isfinite
from numbers import Real
from typing import Literal, Mapping, Sequence

from research.prototype.phase2.corporate_actions import ResolvedCorporateAction


@dataclass(frozen=True)
class CorporateActionLedgerRecord:
    """One auditable account transition or rejected duplicate action."""

    action_id: str
    event_date: date
    phase: Literal["ex_date_open", "payment_date_close"]
    symbol: str
    action_type: str
    shares_before: int
    shares_after: int
    cash_before: float
    cash_after: float
    dividend_receivable_before: float
    dividend_receivable_after: float
    amount: float
    status: Literal["applied", "rejected"]
    reason: str | None


@dataclass(frozen=True)
class CorporateActionApplication:
    """The complete pure account state after applying one event phase."""

    shares: int
    cash: float
    dividend_receivable: float
    receivables_by_action_id: Mapping[str, float]
    opened_action_ids: frozenset[str]
    records: tuple[CorporateActionLedgerRecord, ...]


def _require_non_negative_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _require_finite_non_negative(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return float(value)


def _validate_actions(actions: Sequence[ResolvedCorporateAction], symbol: str) -> None:
    if any(action.symbol != symbol for action in actions):
        raise ValueError("action symbol must match symbol")


def _validate_receivable_state(
    receivables_by_action_id: Mapping[str, float], dividend_receivable: float
) -> dict[str, float]:
    """Reject any scalar/detail receivable state that cannot be fully reconciled."""

    normalized: dict[str, float] = {}
    for action_id, amount in receivables_by_action_id.items():
        if not isinstance(action_id, str) or not action_id.strip():
            raise ValueError("receivable action id must be a non-blank string")
        normalized[action_id] = _require_finite_non_negative(amount, "dividend receivable")
    if not isclose(sum(normalized.values()), dividend_receivable, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("receivable mapping must sum to dividend receivable")
    return normalized


def _record(
    *,
    action: ResolvedCorporateAction,
    event_date: date,
    phase: Literal["ex_date_open", "payment_date_close"],
    shares_before: int,
    shares_after: int,
    cash_before: float,
    cash_after: float,
    receivable_before: float,
    receivable_after: float,
    amount: float,
    status: Literal["applied", "rejected"],
    reason: str | None,
) -> CorporateActionLedgerRecord:
    return CorporateActionLedgerRecord(
        action_id=action.action_id,
        event_date=event_date,
        phase=phase,
        symbol=action.symbol,
        action_type=action.action_type,
        shares_before=shares_before,
        shares_after=shares_after,
        cash_before=cash_before,
        cash_after=cash_after,
        dividend_receivable_before=receivable_before,
        dividend_receivable_after=receivable_after,
        amount=amount,
        status=status,
        reason=reason,
    )


def apply_open_corporate_actions(
    *,
    event_date: date,
    symbol: str,
    shares: int,
    cash: float,
    dividend_receivable: float,
    actions: Sequence[ResolvedCorporateAction],
    record_date_positions: Mapping[str, int],
    receivables_by_action_id: Mapping[str, float],
    opened_action_ids: frozenset[str],
) -> CorporateActionApplication:
    """Apply same-symbol ex-date events before the session's trading begins."""

    current_shares = _require_non_negative_integer(shares, "shares")
    current_cash = _require_finite_non_negative(cash, "cash")
    current_receivable = _require_finite_non_negative(dividend_receivable, "dividend_receivable")
    _validate_actions(actions, symbol)

    current_receivables = _validate_receivable_state(receivables_by_action_id, current_receivable)
    current_opened_ids = frozenset(opened_action_ids)
    records: list[CorporateActionLedgerRecord] = []
    due_actions = sorted(
        (action for action in actions if action.ex_date == event_date), key=lambda action: action.action_id
    )

    for action in due_actions:
        shares_before = current_shares
        cash_before = current_cash
        receivable_before = current_receivable
        if action.action_id in current_opened_ids:
            records.append(
                _record(
                    action=action,
                    event_date=event_date,
                    phase="ex_date_open",
                    shares_before=shares_before,
                    shares_after=current_shares,
                    cash_before=cash_before,
                    cash_after=current_cash,
                    receivable_before=receivable_before,
                    receivable_after=current_receivable,
                    amount=0.0,
                    status="rejected",
                    reason="duplicate_action_id",
                )
            )
            continue

        if action.action_type == "share_ratio_adjustment":
            shares_after = current_shares * action.share_ratio
            if not shares_after.is_integer():
                raise ValueError("non-integral share result")
            current_shares = int(shares_after)
            amount = float(current_shares - shares_before)
        else:
            if action.action_id not in record_date_positions:
                raise ValueError("record date position is required")
            position = _require_non_negative_integer(
                record_date_positions[action.action_id], "record date position"
            )
            amount = position * action.cash_per_share
            current_receivable += amount
            current_receivables[action.action_id] = amount

        current_opened_ids = current_opened_ids | {action.action_id}
        records.append(
            _record(
                action=action,
                event_date=event_date,
                phase="ex_date_open",
                shares_before=shares_before,
                shares_after=current_shares,
                cash_before=cash_before,
                cash_after=current_cash,
                receivable_before=receivable_before,
                receivable_after=current_receivable,
                amount=float(amount),
                status="applied",
                reason=None,
            )
        )

    return CorporateActionApplication(
        shares=current_shares,
        cash=current_cash,
        dividend_receivable=current_receivable,
        receivables_by_action_id=current_receivables,
        opened_action_ids=current_opened_ids,
        records=tuple(records),
    )


def apply_close_corporate_actions(
    *,
    event_date: date,
    symbol: str,
    shares: int,
    cash: float,
    dividend_receivable: float,
    actions: Sequence[ResolvedCorporateAction],
    receivables_by_action_id: Mapping[str, float],
    opened_action_ids: frozenset[str] = frozenset(),
) -> CorporateActionApplication:
    """Settle only matched dividend receivables after their payment-date close."""

    current_shares = _require_non_negative_integer(shares, "shares")
    current_cash = _require_finite_non_negative(cash, "cash")
    current_receivable = _require_finite_non_negative(dividend_receivable, "dividend_receivable")
    _validate_actions(actions, symbol)

    current_receivables = _validate_receivable_state(receivables_by_action_id, current_receivable)
    records: list[CorporateActionLedgerRecord] = []
    due_actions = sorted(
        (
            action
            for action in actions
            if action.action_type == "cash_dividend" and action.payment_date == event_date
        ),
        key=lambda action: action.action_id,
    )

    for action in due_actions:
        if action.action_id not in current_receivables:
            raise ValueError("unrecognized dividend receivable")
        amount = _require_finite_non_negative(
            current_receivables[action.action_id], "dividend receivable"
        )
        if amount > current_receivable:
            raise ValueError("unrecognized dividend receivable")

        shares_before = current_shares
        cash_before = current_cash
        receivable_before = current_receivable
        current_cash += amount
        current_receivable -= amount
        del current_receivables[action.action_id]
        records.append(
            _record(
                action=action,
                event_date=event_date,
                phase="payment_date_close",
                shares_before=shares_before,
                shares_after=current_shares,
                cash_before=cash_before,
                cash_after=current_cash,
                receivable_before=receivable_before,
                receivable_after=current_receivable,
                amount=amount,
                status="applied",
                reason=None,
            )
        )

    return CorporateActionApplication(
        shares=current_shares,
        cash=current_cash,
        dividend_receivable=current_receivable,
        receivables_by_action_id=current_receivables,
        opened_action_ids=frozenset(opened_action_ids),
        records=tuple(records),
    )
