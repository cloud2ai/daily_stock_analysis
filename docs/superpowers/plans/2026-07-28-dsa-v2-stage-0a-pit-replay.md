# DSA v2 阶段 0A 自动 PIT 回放底座 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated, daily A-share PIT replay runner that produces immutable input/state/target/order/fill/NAV audit artifacts without using LLMs, news, or automatic trading.

**Architecture:** `research/prototype/pit_replay` owns availability classification, snapshots, a frozen deterministic baseline strategy, replay orchestration, and JSONL audit files. It reads price history through DSA's `DataFetcherManager`, explicitly labels that unversioned/adjusted source as degraded, and delegates account execution to a backward-compatible portfolio extension in `src/core/target_position_backtest.py`.

**Tech Stack:** Python 3.13, pandas, exchange_calendars, dataclasses, JSONL, pytest.

## Global Constraints

- Keep DSA v0.1 public behavior and all frozen v0.2 T/C/R rules/results unchanged.
- Keep implementation in the isolated research prototype; do not add product DB tables, APIs, Web/Desktop work, LLMs, NewsGrab, or live order placement.
- Use `DataFetcherManager` as the only price retrieval path; do not introduce another market-data provider.
- Treat missing/unprovable availability as explicit exclusion or degraded status, never as no event/no risk.
- Preserve t-close decision and t+1-open execution; retain rejected orders.
- Do not commit unless the user separately authorizes a commit.

---

## File Structure

- `research/prototype/pit_replay/models.py`: immutable request, price/disclosure observation, snapshot, strategy result, and quality records.
- `research/prototype/pit_replay/availability.py`: decision-cutoff eligibility and conservative date-only disclosure rules.
- `research/prototype/pit_replay/strategy.py`: pure frozen 20-session momentum baseline, explicitly versioned and free of T/C/R logic.
- `research/prototype/pit_replay/audit.py`: content-hashed JSONL/manifest writer.
- `research/prototype/pit_replay/runner.py`: price/disclosure sources, date traversal, snapshots, target sequence, execution handoff, CLI.
- `src/core/target_position_backtest.py`: additive shared-cash, multi-symbol execution API that reuses the same cost/slippage/lot calculations.
- `tests/test_pit_replay_availability.py`, `tests/test_pit_replay_runner.py`, `tests/test_target_position_backtest.py`: behavioral regression coverage.

### Task 1: PIT observation and disclosure eligibility contract

**Files:**
- Create: `research/prototype/pit_replay/__init__.py`
- Create: `research/prototype/pit_replay/models.py`
- Create: `research/prototype/pit_replay/availability.py`
- Test: `tests/test_pit_replay_availability.py`

**Interfaces:**
- Produces `DisclosureObservation`, `AvailabilityDecision`, and `is_disclosure_visible(observation, cutoff) -> AvailabilityDecision`.
- `DisclosureObservation` contains a timezone-aware `published_at` or a `published_date`; the two are mutually exclusive.

- [ ] **Step 1: Write failing tests for exact-time, date-only, and missing disclosure availability**

```python
def test_date_only_disclosure_is_not_visible_until_next_session() -> None:
    item = DisclosureObservation(symbol="600519", kind="announcement", published_date=date(2024, 1, 2))
    assert not is_disclosure_visible(item, shanghai_cutoff(2024, 1, 2)).included
    assert is_disclosure_visible(item, shanghai_cutoff(2024, 1, 3)).included

def test_missing_disclosure_time_is_explicitly_excluded() -> None:
    decision = is_disclosure_visible(DisclosureObservation(symbol="600519", kind="announcement"), shanghai_cutoff(2024, 1, 3))
    assert (decision.included, decision.reason) == (False, "unavailable_pit_metadata")
```

- [ ] **Step 2: Run the availability tests and verify they fail because the package is absent**

Run: `python -m pytest tests/test_pit_replay_availability.py -q`

- [ ] **Step 3: Implement immutable observations and the smallest conservative eligibility function**

```python
def is_disclosure_visible(observation: DisclosureObservation, cutoff: datetime) -> AvailabilityDecision:
    if observation.published_at is not None:
        return AvailabilityDecision(observation.published_at <= cutoff, "published_at_before_cutoff")
    if observation.published_date is not None:
        return AvailabilityDecision(observation.published_date < cutoff.date(), "date_only_wait_next_session")
    return AvailabilityDecision(False, "unavailable_pit_metadata")
```

- [ ] **Step 4: Run the availability tests and verify they pass**

Run: `python -m pytest tests/test_pit_replay_availability.py -q`

### Task 2: Add a backward-compatible shared-cash portfolio execution API

**Files:**
- Modify: `src/core/target_position_backtest.py`
- Modify: `tests/test_target_position_backtest.py`

**Interfaces:**
- Produces `PortfolioDailyTargetWeights(signal_date: date, target_weights: Mapping[str, float])`.
- Produces `run_portfolio_target_position_backtest(bars_by_symbol, target_weights, assumptions) -> PortfolioTargetPositionBacktestResult`.
- The existing `run_target_position_backtest` signature and result must remain unchanged.

- [ ] **Step 1: Write a failing portfolio test for pooled cash and sell-before-buy rebalancing**

```python
result = run_portfolio_target_position_backtest(
    bars_by_symbol={"AAA": aaa_bars, "BBB": bbb_bars},
    target_weights=[PortfolioDailyTargetWeights(date(2024, 1, 2), {"AAA": 0.5, "BBB": 0.5})],
    assumptions=assumptions,
)
assert {trade.symbol for trade in result.trades if trade.status == "filled"} == {"AAA", "BBB"}
assert result.daily_nav[-1].cash >= 0
```

- [ ] **Step 2: Run the selected portfolio test and verify it fails because the API is absent**

Run: `python -m pytest tests/test_target_position_backtest.py::test_portfolio_rebalances_shared_cash_at_next_open -q`

- [ ] **Step 3: Add only the portfolio dataclasses and execution path, reusing existing fee/slippage/lot helpers**

```python
def run_portfolio_target_position_backtest(*, bars_by_symbol, target_weights, assumptions):
    """Simulate frozen same-day portfolio targets using one cash ledger."""
    # At each t+1 open: sell reductions, scale all buys deterministically, then mark NAV at close.
```

- [ ] **Step 4: Run the portfolio test and the existing single-symbol regression suite**

Run: `python -m pytest tests/test_target_position_backtest.py -q`

### Task 3: Build the pure automatic replay loop and immutable audit writer

**Files:**
- Create: `research/prototype/pit_replay/strategy.py`
- Create: `research/prototype/pit_replay/audit.py`
- Create: `research/prototype/pit_replay/runner.py`
- Test: `tests/test_pit_replay_runner.py`

**Interfaces:**
- `ReplayPriceSource.load(symbol, start, end) -> PriceSeries` is dependency-injected; the runner has no direct network logic.
- `run_replay(request, price_source, disclosure_source, strategy, output_root) -> ReplayRunResult`.
- `Momentum20Strategy` reads only snapshot price history and returns an explicit strategy version plus a target in `[0, 0.5]`.

- [ ] **Step 1: Write a failing replay test proving t-close data yields a t+1 fill and all required artifacts**

```python
result = run_replay(request, FakePriceSource(two_symbols), EmptyDisclosureSource(), Momentum20Strategy(), tmp_path)
assert result.manifest["execution_timing"] == "t_close_to_t_plus_1_open"
assert (result.output_dir / "input_snapshots.jsonl").exists()
assert (result.output_dir / "targets.jsonl").exists()
assert (result.output_dir / "orders.jsonl").exists()
assert (result.output_dir / "fills.jsonl").exists()
assert (result.output_dir / "nav.jsonl").exists()
```

- [ ] **Step 2: Run the replay test and verify it fails because `run_replay` is absent**

Run: `python -m pytest tests/test_pit_replay_runner.py::test_replay_writes_timed_audit_artifacts -q`

- [ ] **Step 3: Implement the smallest deterministic loop and writer**

```python
for as_of in calendar_sessions:
    snapshot = build_snapshot(as_of=as_of, cutoff=request.cutoff_for(as_of))
    strategy_result = strategy.evaluate(snapshot=snapshot, previous_state=state)
    write_snapshot_and_target(snapshot, strategy_result)
# Then hand all frozen daily targets to the portfolio engine for t+1-open fills.
```

- [ ] **Step 4: Add a failing test for a same-date date-only disclosure and verify its exclusion appears in the snapshot**

```python
assert snapshot["disclosures"][0]["availability_reason"] == "date_only_wait_next_session"
assert snapshot["disclosures"][0]["included"] is False
```

- [ ] **Step 5: Implement the audit inclusion/exclusion record and run both replay tests**

Run: `python -m pytest tests/test_pit_replay_runner.py -q`

### Task 4: Connect the runner to DSA's unified daily-data manager and expose the research CLI

**Files:**
- Modify: `research/prototype/pit_replay/runner.py`
- Modify: `tests/test_pit_replay_runner.py`
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- `DsaDailyPriceSource(manager: DataFetcherManager)` calls only `manager.get_daily_data` and records returned source name.
- CLI: `python -m research.prototype.pit_replay.runner --stocks 600519,300750 --start YYYY-MM-DD --end YYYY-MM-DD --decision-cutoff 15:30:00+08:00 --strategy pit-baseline-momentum20-v1 --output-dir research/local/pit_replay`.
- Every real-data manifest carries `adjusted_price_simulation` and `inferred_price_availability` unless a future source provides stronger metadata.

- [ ] **Step 1: Write a failing source-adapter test with a real in-memory manager substitute**

```python
source = DsaDailyPriceSource(manager=RecordingManager(frame, "TencentFetcher"))
series = source.load("600519", date(2024, 1, 1), date(2024, 1, 31))
assert series.quality_status == "adjusted_price_simulation"
assert series.source_name == "TencentFetcher"
```

- [ ] **Step 2: Run the source-adapter test and verify it fails because the adapter is absent**

Run: `python -m pytest tests/test_pit_replay_runner.py::test_dsa_price_source_marks_unversioned_history_degraded -q`

- [ ] **Step 3: Implement the manager adapter and CLI argument validation without adding a new provider**

```python
frame, source_name = self.manager.get_daily_data(symbol, start_date=start.isoformat(), end_date=end.isoformat())
return PriceSeries.from_frame(frame, source_name=source_name, quality_status="adjusted_price_simulation")
```

- [ ] **Step 4: Run the PIT suite, legacy engine suite, compilation, and the repository CI gate**

Run: `python -m pytest tests/test_pit_replay_availability.py tests/test_pit_replay_runner.py tests/test_target_position_backtest.py -q`

Run: `python -m py_compile research/prototype/pit_replay/*.py src/core/target_position_backtest.py`

Run: `./scripts/ci_gate.sh`

## Plan Self-Review

- Spec coverage: Tasks 1 and 3 implement cutoff, disclosure downgrade, state/target/audit, and stop-status recording; Task 2 provides pooled NAV and existing-engine reuse; Task 4 provides the unified DSA data entry point and explicit adjusted-price downgrade.
- Scope: No task adds database storage, UI/API, NewsGrab, LLM, strategy optimization, or real orders.
- Type consistency: Task 1 observations feed Task 3 snapshots; Task 2 consumes per-date symbol weights generated by Task 3; Task 4 supplies Task 3's price-source protocol.
- No-placeholder scan: no deferred behavior is needed for the stated 0A vertical slice; future stricter price/corporate-action data is an explicit runtime downgrade, not an implementation omission.
