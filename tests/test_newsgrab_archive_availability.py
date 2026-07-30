from datetime import datetime

import pytest

from research.prototype.newsgrab_archive.availability import classify_strict_pit
from research.prototype.newsgrab_archive.models import ArchiveRequest


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_date_only_news_is_not_strict_pit_even_when_archived_before_cutoff():
    result = classify_strict_pit(
        "2026-07-28",
        archived_at=utc("2026-07-28T07:00:00Z"),
        decision_cutoff=utc("2026-07-28T08:00:00Z"),
    )

    assert result.status == "unavailable_date_only"
    assert result.published_at_precision == "date_only"


def test_exact_news_requires_publication_and_archive_before_cutoff():
    result = classify_strict_pit(
        "2026-07-28T07:30:00Z",
        archived_at=utc("2026-07-28T07:45:00Z"),
        decision_cutoff=utc("2026-07-28T08:00:00Z"),
    )

    assert result.status == "eligible"
    assert result.published_at == utc("2026-07-28T07:30:00Z")


def test_exact_news_archived_after_cutoff_is_not_strict_pit():
    result = classify_strict_pit(
        "2026-07-28T07:30:00Z",
        archived_at=utc("2026-07-28T08:01:00Z"),
        decision_cutoff=utc("2026-07-28T08:00:00Z"),
    )

    assert result.status == "unavailable_archived_after_cutoff"


def test_missing_or_invalid_publication_metadata_is_explicitly_unavailable():
    missing = classify_strict_pit(
        None,
        archived_at=utc("2026-07-28T07:00:00Z"),
        decision_cutoff=utc("2026-07-28T08:00:00Z"),
    )
    invalid = classify_strict_pit(
        "not-a-time",
        archived_at=utc("2026-07-28T07:00:00Z"),
        decision_cutoff=utc("2026-07-28T08:00:00Z"),
    )

    assert missing.status == "unavailable_missing_published_at"
    assert invalid.status == "unavailable_invalid_published_at"


def test_archive_request_rejects_multi_day_historical_search_windows():
    with pytest.raises(ValueError, match="days must be exactly 1"):
        ArchiveRequest(query="600519", scope="company", days=2)
