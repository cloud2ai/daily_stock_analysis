"""Stable data contracts for the NewsGrab forward archive prototype."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Tuple


VALID_SCOPES = frozenset({"company", "industry", "market"})


@dataclass(frozen=True)
class ArchiveRequest:
    """One DSA-owned NewsGrab collection request."""

    query: str
    scope: str
    language: str = "zh-CN"
    region: str = "CN"
    max_results: int = 10
    days: int = 1

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be blank")
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"scope must be one of {sorted(VALID_SCOPES)}")
        if not self.language.strip() or not self.region.strip():
            raise ValueError("language and region must not be blank")
        if self.max_results < 1:
            raise ValueError("max_results must be positive")
        if self.days != 1:
            raise ValueError("days must be exactly 1 for forward-only capture")

    def to_newsgrab_payload(self) -> Mapping[str, Any]:
        return {
            "backend": "google_news",
            "query": self.query,
            "params": {
                "max_results": self.max_results,
                "days": self.days,
                "language": self.language,
                "region": self.region,
            },
        }


@dataclass(frozen=True)
class Availability:
    """Strict PIT availability of one archived news item."""

    status: str
    published_at: Optional[datetime]
    published_at_precision: str
    reason: str


@dataclass(frozen=True)
class JobResult:
    """Terminal result of one NewsGrab asynchronous job."""

    job_id: Optional[str]
    submitted_at: datetime
    completed_at: datetime
    articles: Tuple[Any, ...]
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None
