"""Frozen deterministic research strategies for PIT replay."""

from __future__ import annotations

from typing import Any, Mapping

from .models import PITSnapshot, StrategyResult


class Momentum20Strategy:
    """A frozen 20-session direction baseline, separate from v0.1/T/C/R rules."""

    version = "pit-baseline-momentum20-v1"

    def evaluate(
        self, snapshot: PITSnapshot, previous_state: Mapping[str, Any]
    ) -> StrategyResult:
        targets: dict[str, float] = {}
        reasons: dict[str, str] = {}
        momentum: dict[str, float | None] = {}
        for symbol, history in snapshot.price_history.items():
            if len(history) < 21:
                targets[symbol] = 0.0
                reasons[symbol] = "insufficient_price_history"
                momentum[symbol] = None
                continue
            value = history[-1].close / history[-21].close - 1.0
            momentum[symbol] = value
            if value >= 0.05:
                targets[symbol] = 0.5
                reasons[symbol] = "momentum20_buy_permission"
            elif value <= -0.05:
                targets[symbol] = 0.0
                reasons[symbol] = "momentum20_sell_exit"
            else:
                targets[symbol] = float(previous_state.get("targets", {}).get(symbol, 0.0))
                reasons[symbol] = "momentum20_hold_previous_target"
        return StrategyResult(
            state={"targets": targets, "momentum20": momentum},
            target_weights=targets,
            reasons=reasons,
        )
