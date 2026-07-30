"""Validated point-in-time corporate-action inputs for the Phase 2 prototype."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from hashlib import sha256
import json
from math import isfinite
from numbers import Real
from typing import Literal, Sequence
from zoneinfo import ZoneInfo

from research.prototype.pit_replay.models import PriceObservation


_SUPPORTED_ACTION_TYPES = frozenset({"cash_dividend", "share_ratio_adjustment"})


def _text(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must not be blank")
    return result


def _timestamp(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(_text(value, field))
        except ValueError as exc:
            raise ValueError(f"invalid {field}") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{field} must include timezone")
    return result


def _day(value: object, field: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{field} must be a date")
    return value


def _finite(value: object, field: str, *, positive: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if positive and result <= 0:
        raise ValueError(f"{field} must be positive")
    if not positive and result < 0:
        raise ValueError(f"{field} must be non-negative")
    return result


@dataclass(frozen=True)
class ResolvedCorporateAction:
    action_id: str
    symbol: str
    action_type: Literal["cash_dividend", "share_ratio_adjustment"]
    announced_at: datetime
    ex_date: date
    payment_date: date | None
    cash_per_share: float | None
    share_ratio: float | None
    price_adjustment_ratio: float | None
    currency: str
    evidence_ids: tuple[str, ...]
    resolver_version: str
    input_sha256: str
    pit_status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_id", _text(self.action_id, "action_id"))
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol"))
        object.__setattr__(self, "announced_at", _timestamp(self.announced_at, "announced_at"))
        object.__setattr__(self, "ex_date", _day(self.ex_date, "ex_date"))
        object.__setattr__(self, "currency", _text(self.currency, "currency"))
        object.__setattr__(self, "resolver_version", _text(self.resolver_version, "resolver_version"))
        object.__setattr__(self, "input_sha256", _text(self.input_sha256, "input_sha256"))

        evidence_ids = tuple(_text(item, "evidence_ids") for item in self.evidence_ids)
        if not evidence_ids:
            raise ValueError("evidence_ids must not be empty")
        object.__setattr__(self, "evidence_ids", evidence_ids)

        if self.action_type not in _SUPPORTED_ACTION_TYPES:
            raise ValueError("unsupported corporate action type")
        if self.pit_status != "strict_pit":
            raise ValueError("pit_status must be strict_pit")

        if self.price_adjustment_ratio is not None:
            object.__setattr__(
                self,
                "price_adjustment_ratio",
                _finite(self.price_adjustment_ratio, "price_adjustment_ratio", positive=True),
            )

        if self.action_type == "cash_dividend":
            if self.share_ratio is not None:
                raise ValueError("share_ratio must be None for cash_dividend")
            if self.cash_per_share is None:
                raise ValueError("cash_per_share is required for cash_dividend")
            object.__setattr__(
                self, "cash_per_share", _finite(self.cash_per_share, "cash_per_share", positive=False)
            )
            if self.payment_date is None:
                raise ValueError("payment_date is required for cash_dividend")
            payment_date = _day(self.payment_date, "payment_date")
            if payment_date < self.ex_date:
                raise ValueError("payment_date must not be before ex_date")
            object.__setattr__(self, "payment_date", payment_date)
            return

        if self.payment_date is not None:
            raise ValueError("payment_date must be None for share_ratio_adjustment")
        if self.cash_per_share is not None:
            raise ValueError("cash_per_share must be None for share_ratio_adjustment")
        if self.share_ratio is None:
            raise ValueError("share_ratio is required for share_ratio_adjustment")
        object.__setattr__(self, "share_ratio", _finite(self.share_ratio, "share_ratio", positive=True))


@dataclass(frozen=True)
class AdjustmentFactorRow:
    """One strict-PIT adjustment factor applicable to a completed session."""

    symbol: str
    as_of: date
    factor: float
    action_ids: tuple[str, ...]
    factor_version: str
    input_sha256: str
    pit_status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol"))
        object.__setattr__(self, "as_of", _day(self.as_of, "as_of"))
        object.__setattr__(self, "factor", _finite(self.factor, "factor", positive=True))
        object.__setattr__(
            self, "action_ids", tuple(_text(action_id, "action_ids") for action_id in self.action_ids)
        )
        object.__setattr__(self, "factor_version", _text(self.factor_version, "factor_version"))
        object.__setattr__(self, "input_sha256", _text(self.input_sha256, "input_sha256"))
        if self.pit_status != "strict_pit":
            raise ValueError("pit_status must be strict_pit")


def visible_actions_as_of(
    actions: Sequence[ResolvedCorporateAction], *, as_of: date, decision_cutoff: datetime
) -> tuple[ResolvedCorporateAction, ...]:
    """Return actions visible by the decision cutoff and effective by ``as_of``."""

    cutoff = _timestamp(decision_cutoff, "decision_cutoff")
    session = _day(as_of, "as_of")
    return tuple(
        sorted(
            (
                action
                for action in actions
                if action.announced_at <= cutoff and action.ex_date <= session
            ),
            key=lambda action: (action.ex_date, action.action_id),
        )
    )


def _factor_input_sha256(
    *, symbol: str, session: date, visible_actions: Sequence[ResolvedCorporateAction], factor: float
) -> str:
    payload = {
        "action_ids": [action.action_id for action in visible_actions],
        "action_input_sha256": [action.input_sha256 for action in visible_actions],
        "factor": factor,
        "session": session.isoformat(),
        "symbol": symbol,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_adjustment_factor_rows(
    *,
    symbol: str,
    sessions: Sequence[date],
    actions: Sequence[ResolvedCorporateAction],
    decision_cutoff_time: time,
    timezone_name: str,
) -> tuple[AdjustmentFactorRow, ...]:
    """Project strict-PIT price factors independently for each session."""

    resolved_symbol = _text(symbol, "symbol")
    normalized_sessions = tuple(_day(session, "sessions") for session in sessions)
    if tuple(sorted(normalized_sessions)) != normalized_sessions or len(set(normalized_sessions)) != len(
        normalized_sessions
    ):
        raise ValueError("sessions must be in ascending order without duplicates")
    if isinstance(decision_cutoff_time, datetime) or not isinstance(decision_cutoff_time, time):
        raise ValueError("decision_cutoff_time must be a time")
    zone = ZoneInfo(_text(timezone_name, "timezone_name"))
    if any(action.symbol != resolved_symbol for action in actions):
        raise ValueError("action symbols must match symbol")
    if len({action.action_id for action in actions}) != len(actions):
        raise ValueError("duplicate action_id")

    rows: list[AdjustmentFactorRow] = []
    for session in normalized_sessions:
        visible_actions = visible_actions_as_of(
            actions,
            as_of=session,
            decision_cutoff=datetime.combine(session, decision_cutoff_time, zone),
        )
        factor = 1.0
        for action in visible_actions:
            if action.action_type == "cash_dividend" and action.price_adjustment_ratio is None:
                raise ValueError("cash dividend requires explicit price adjustment evidence")
            if action.action_type == "share_ratio_adjustment":
                factor *= action.share_ratio  # Validated by ResolvedCorporateAction.
            if action.price_adjustment_ratio is not None:
                factor *= action.price_adjustment_ratio
        rows.append(
            AdjustmentFactorRow(
                symbol=resolved_symbol,
                as_of=session,
                factor=factor,
                action_ids=tuple(action.action_id for action in visible_actions),
                factor_version="phase2-factor-v1",
                input_sha256=_factor_input_sha256(
                    symbol=resolved_symbol,
                    session=session,
                    visible_actions=visible_actions,
                    factor=factor,
                ),
                pit_status="strict_pit",
            )
        )
    return tuple(rows)


def apply_adjustment_factors(
    *, observations: Sequence[PriceObservation], factors: Sequence[AdjustmentFactorRow]
) -> tuple[PriceObservation, ...]:
    """Apply date-matched factors to OHLC while retaining raw volume and ordering."""

    factors_by_date: dict[date, AdjustmentFactorRow] = {}
    for factor in factors:
        if factor.as_of in factors_by_date:
            raise ValueError("duplicate adjustment factor")
        factors_by_date[factor.as_of] = factor

    adjusted: list[PriceObservation] = []
    for observation in observations:
        factor = factors_by_date.get(observation.date)
        if factor is None:
            raise ValueError("missing adjustment factor")
        if factor.symbol != observation.symbol:
            raise ValueError("adjustment factor symbol must match observation symbol")
        adjusted.append(
            PriceObservation(
                symbol=observation.symbol,
                date=observation.date,
                open=observation.open * factor.factor,
                high=observation.high * factor.factor,
                low=observation.low * factor.factor,
                close=observation.close * factor.factor,
                volume=observation.volume,
            )
        )
    return tuple(adjusted)
