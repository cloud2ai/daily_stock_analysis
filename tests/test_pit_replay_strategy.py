from datetime import date, datetime
from zoneinfo import ZoneInfo

from research.prototype.pit_replay import models, strategy


def test_momentum_strategy_uses_only_snapshot_history_for_target() -> None:
    observations = tuple(
        models.PriceObservation(
            symbol="600519",
            date=date(2024, 1, 2 + index),
            open=100.0 + index * 0.3,
            high=100.0 + index * 0.3,
            low=100.0 + index * 0.3,
            close=100.0 + index * 0.3,
            volume=1_000,
        )
        for index in range(21)
    )
    snapshot = models.PITSnapshot(
        as_of=observations[-1].date,
        decision_cutoff=datetime(2024, 1, 22, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        price_history={"600519": observations},
        disclosures={"600519": ()},
        price_quality={"600519": "contract_validated"},
    )

    result = strategy.Momentum20Strategy().evaluate(snapshot, {})

    assert result.target_weights == {"600519": 0.5}
    assert result.reasons == {"600519": "momentum20_buy_permission"}
