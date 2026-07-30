from datetime import date, datetime
from zoneinfo import ZoneInfo

from research.prototype.pit_replay import availability, models


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _cutoff(day: date, hour: int = 15, minute: int = 30) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI)


def test_exactly_timed_disclosure_is_visible_only_by_decision_cutoff() -> None:
    item = models.DisclosureObservation(
        symbol="600519",
        kind="announcement",
        published_at=datetime(2024, 1, 2, 16, 0, tzinfo=SHANGHAI),
    )

    assert not availability.is_disclosure_visible(item, _cutoff(date(2024, 1, 2))).included
    assert availability.is_disclosure_visible(item, _cutoff(date(2024, 1, 3))).included


def test_date_only_disclosure_waits_until_a_later_decision_day() -> None:
    item = models.DisclosureObservation(
        symbol="600519",
        kind="announcement",
        published_date=date(2024, 1, 2),
    )

    assert not availability.is_disclosure_visible(item, _cutoff(date(2024, 1, 2))).included
    decision = availability.is_disclosure_visible(item, _cutoff(date(2024, 1, 3)))
    assert (decision.included, decision.reason) == (True, "date_only_visible_after_publish_date")


def test_missing_disclosure_availability_is_explicitly_excluded() -> None:
    decision = availability.is_disclosure_visible(
        models.DisclosureObservation(symbol="600519", kind="announcement"),
        _cutoff(date(2024, 1, 3)),
    )

    assert (decision.included, decision.reason) == (False, "unavailable_pit_metadata")
