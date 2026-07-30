"""PIT replay data contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Mapping, Optional, Tuple

from src.core.target_position_backtest import BacktestAssumptions


@dataclass(frozen=True)
class DisclosureObservation:
    """One announcement or fundamental disclosure and its publication evidence."""

    symbol: str
    kind: str
    published_at: Optional[datetime] = None
    published_date: Optional[date] = None

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol must not be blank")
        if not self.kind.strip():
            raise ValueError("kind must not be blank")
        if self.published_at is not None and self.published_date is not None:
            raise ValueError("published_at and published_date are mutually exclusive")
        if self.published_at is not None and (
            self.published_at.tzinfo is None or self.published_at.utcoffset() is None
        ):
            raise ValueError("published_at must be timezone-aware")


@dataclass(frozen=True)
class PriceObservation:
    """One completed daily bar supplied through DSA's price contract."""

    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class PriceSeries:
    """One symbol's ordered price observations and their evidence quality."""

    symbol: str
    source_name: str
    quality_status: str
    observations: Tuple[PriceObservation, ...]

    def __post_init__(self) -> None:
        if not self.observations:
            raise ValueError("price observations must not be empty")
        if any(item.symbol != self.symbol for item in self.observations):
            raise ValueError("price observation symbols must match the series symbol")
        if tuple(sorted(self.observations, key=lambda item: item.date)) != self.observations:
            raise ValueError("price observations must be ordered by date")


@dataclass(frozen=True)
class ReplayRequest:
    """Frozen inputs for one daily PIT replay run."""

    symbols: Tuple[str, ...]
    start: date
    end: date
    decision_cutoff_time: time
    assumptions: BacktestAssumptions
    timezone_name: str = "Asia/Shanghai"
    warmup_calendar_days: int = 120

    def __post_init__(self) -> None:
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must be a non-empty unique sequence")
        if any(not symbol.strip() for symbol in self.symbols):
            raise ValueError("symbols must not be blank")
        if self.start > self.end:
            raise ValueError("start must not be after end")
        if self.warmup_calendar_days < 0:
            raise ValueError("warmup_calendar_days must not be negative")


@dataclass(frozen=True)
class SnapshotDisclosure:
    observation: DisclosureObservation
    included: bool
    availability_reason: str


@dataclass(frozen=True)
class PITSnapshot:
    as_of: date
    decision_cutoff: datetime
    price_history: Mapping[str, Tuple[PriceObservation, ...]]
    disclosures: Mapping[str, Tuple[DisclosureObservation, ...]]
    price_quality: Mapping[str, str]


@dataclass(frozen=True)
class StrategyResult:
    state: Mapping[str, Any]
    target_weights: Mapping[str, float]
    reasons: Mapping[str, str]
