# Multi-Agent Insights Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface each `full`-mode agent's opinion (Technical/Intel/MacroIntel/Risk) and an aggregated bullish/bearish view on the report page in `apps/dsa-web`, backed by a new backend capture step that serializes `ctx.opinions` (currently discarded after pipeline execution) into the dashboard.

**Architecture:** Backend: `AgentOrchestrator.run()` serializes `ctx.opinions` into `dashboard["agent_opinions"]`; `AnalysisResult` gains two new getters (`get_agent_opinions()`, `get_bullish_bearish_points()`); `analysis_service.py::_build_analysis_response` adds a new `report["multi_agent_insights"]` block. Frontend: new TypeScript types, i18n text, and a `MultiAgentInsights.tsx` component wired into `ReportSummary.tsx` right after `ReportStrategy`. The existing `toCamelCase()` transform in `apps/dsa-web/src/api/analysis.ts` means backend fields can stay snake_case; no manual case conversion needed.

**Tech Stack:** Python (existing `src/agent/`, `src/analyzer.py`, `src/services/`), TypeScript/React (existing `apps/dsa-web`, Vite + Vitest + Testing Library).

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-07-27-multi-agent-insights-web-ui-design.md`:

- **Scope**: real-time analysis response path only (`_build_analysis_response`). History-page wiring (`historyApi.getMarkdown`) is explicitly out of scope for this plan.
- **Bull/bear aggregation mapping** (exact, do not deviate):
  | Agent | Bullish source | Bearish source |
  |---|---|---|
  | `intel` | `raw_data.positive_catalysts` | `raw_data.risk_alerts` |
  | `macro_intel` | `raw_data.bullish_points` | `raw_data.bearish_points` |
  | `risk` | (none) | `raw_data.flags[].description` |
  | `technical` | (none) | (none) |
- Every aggregated bullish/bearish item carries a `source_agent` tag (not anonymous).
- `signal_attribution`'s four buckets (`technical_indicators`/`news_sentiment`/`fundamentals`/`market_conditions`) are passed through as-is — do NOT add a 5th "macro" bucket to this schema in this plan.
- Missing/malformed data degrades to empty lists/`None` everywhere — never raises, matching the existing `get_sniper_points()`-style getter convention in `src/analyzer.py`.
- Frontend component returns `null` when there's nothing to show (matches `ReportStrategy`'s `if (!strategy) return null` pattern) — do not render an empty shell.
- i18n: zh/en/ko only, following `apps/dsa-web/src/utils/reportLanguage.ts`'s existing `REPORT_TEXT` dictionary pattern (no new i18n mechanism).
- Commit messages in English, no `Co-Authored-By` trailer. Backend changes validated with `python -m py_compile` + `pytest`; frontend changes validated with `npm run lint` + `npm run build` + `npm test` (per AGENTS.md's Web 前端改动 verification matrix).

---

### Task 1: Capture `ctx.opinions` into the dashboard

**Files:**
- Modify: `src/agent/orchestrator.py:352-375` (`AgentOrchestrator.run()`)
- Test: `tests/test_multi_agent.py`

**Interfaces:**
- Consumes: existing `ctx.opinions: List[AgentOpinion]` (`src/agent/protocols.py`), existing `AgentOpinion` fields (`agent_name`, `signal`, `confidence`, `reasoning`).
- Produces (used by Task 2): `dashboard["agent_opinions"]` — a list of `{"agent_name": str, "signal": str, "confidence": float, "reasoning": str}` dicts, present on the `dict` returned as `AgentResult.dashboard` from `AgentOrchestrator.run()`. Only present when `dashboard` is a `dict` (i.e. `parse_dashboard=True` succeeded); absent/unchanged otherwise.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_multi_agent.py` (find the existing `class TestOrchestratorExecution` or add a new focused test class near it — search for `class TestOrchestratorExecution` to place this test inside that class, matching its existing setup helpers):

```python
def test_run_serializes_ctx_opinions_into_dashboard_agent_opinions(self):
    from src.agent.orchestrator import AgentOrchestrator
    from src.agent.protocols import AgentOpinion

    orch = AgentOrchestrator(
        tool_registry=MagicMock(),
        llm_adapter=MagicMock(),
        mode="full",
    )

    fake_opinions = [
        AgentOpinion(agent_name="technical", signal="buy", confidence=0.72, reasoning="MA金叉"),
        AgentOpinion(agent_name="intel", signal="hold", confidence=0.55, reasoning="消息面中性"),
        AgentOpinion(agent_name="macro_intel", signal="hold", confidence=0.60, reasoning="宏观缺乏强催化"),
    ]

    def fake_execute_pipeline(ctx, parse_dashboard=True):
        from src.agent.orchestrator import OrchestratorResult
        ctx.opinions.extend(fake_opinions)
        return OrchestratorResult(
            success=True,
            content="{}",
            dashboard={"core_conclusion": {"one_sentence": "test"}},
            tool_calls_log=[],
            total_steps=1,
            total_tokens=0,
            provider="test",
            model="test",
            error=None,
            runtime_facts=None,
        )

    with patch.object(orch, "_execute_pipeline", side_effect=fake_execute_pipeline):
        result = orch.run("analyze this stock", context={"stock_code": "600019", "stock_name": "宝钢股份"})

    assert result.dashboard is not None
    assert result.dashboard["agent_opinions"] == [
        {"agent_name": "technical", "signal": "buy", "confidence": 0.72, "reasoning": "MA金叉"},
        {"agent_name": "intel", "signal": "hold", "confidence": 0.55, "reasoning": "消息面中性"},
        {"agent_name": "macro_intel", "signal": "hold", "confidence": 0.60, "reasoning": "宏观缺乏强催化"},
    ]
    # core_conclusion (an existing dashboard field) must survive untouched
    assert result.dashboard["core_conclusion"]["one_sentence"] == "test"
```

Check the top of `tests/test_multi_agent.py` for the exact existing import style of `MagicMock`/`patch` (already imported per the file's existing content) and `OrchestratorResult` (check `src/agent/orchestrator.py` for its exact dataclass field names before writing this test — match them exactly; the fields shown above mirror the ones already used in `AgentOrchestrator.run()`'s own `AgentResult(...)` construction, see the current file content around line 364-375).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_multi_agent.py -v -k test_run_serializes_ctx_opinions_into_dashboard_agent_opinions`
Expected: FAIL — `KeyError: 'agent_opinions'` (the dashboard dict doesn't have this key yet).

- [ ] **Step 3: Implement the capture step**

In `src/agent/orchestrator.py`, change `run()`:

```python
    def run(self, task: str, context: Optional[Dict[str, Any]] = None) -> "AgentResult":
        """Run the multi-agent pipeline for a dashboard analysis.

        Returns an ``AgentResult`` (same type as ``AgentExecutor.run``).
        """
        from src.agent.executor import AgentResult

        ctx = self._build_context(task, context)
        ctx.meta["response_mode"] = "dashboard"
        orch_result = self._execute_pipeline(ctx, parse_dashboard=True)

        dashboard = orch_result.dashboard
        if isinstance(dashboard, dict):
            dashboard["agent_opinions"] = [
                {
                    "agent_name": op.agent_name,
                    "signal": op.signal,
                    "confidence": op.confidence,
                    "reasoning": op.reasoning,
                }
                for op in ctx.opinions
            ]

        return AgentResult(
            success=orch_result.success,
            content=orch_result.content,
            dashboard=dashboard,
            tool_calls_log=orch_result.tool_calls_log,
            total_steps=orch_result.total_steps,
            total_tokens=orch_result.total_tokens,
            provider=orch_result.provider,
            model=orch_result.model,
            error=orch_result.error,
            runtime_facts=orch_result.runtime_facts,
        )
```

This only touches `run()` (the dashboard-report entry point) — `chat()`/`execute_turn()` (chat mode, a different response shape) are untouched per the design's real-time-analysis-only scope.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_multi_agent.py -v -k test_run_serializes_ctx_opinions_into_dashboard_agent_opinions`
Expected: PASS.

- [ ] **Step 5: Run the full orchestrator test file to confirm no regressions**

Run: `python3 -m pytest tests/test_multi_agent.py -v`
Expected: all tests pass (no change to any other `run()` caller's expectations — this is a pure addition to the returned dict).

- [ ] **Step 6: Commit**

```bash
git add src/agent/orchestrator.py tests/test_multi_agent.py
git commit -m "feat: serialize ctx.opinions into dashboard[agent_opinions]"
```

---

### Task 2: `AnalysisResult` getters for agent opinions and bull/bear aggregation

**Files:**
- Modify: `src/analyzer.py` (near `get_risk_alerts` at line ~1809, inside the `AnalysisResult` dataclass starting at line 1670)
- Test: `tests/test_analysis_result_agent_opinions.py` (new file)

**Interfaces:**
- Consumes: `self.dashboard["agent_opinions"]` (Task 1's new field), each opinion's `raw_data` (not itself captured by Task 1 — this task reads `raw_data` a different way, see Step 3 note below).
- Produces (used by Task 3): `AnalysisResult.get_agent_opinions() -> List[Dict[str, Any]]`, `AnalysisResult.get_bullish_bearish_points() -> Dict[str, List[Dict[str, str]]]` (keys `"bullish"`/`"bearish"`, each a list of `{"text": str, "source_agent": str}`).

**Important note on `raw_data`**: Task 1's capture step only serialized `agent_name`/`signal`/`confidence`/`reasoning` into `dashboard["agent_opinions"]` — it deliberately did NOT include each opinion's full `raw_data` (which can be large and agent-specific). But `get_bullish_bearish_points()` needs `positive_catalysts`/`risk_alerts`/`bullish_points`/`bearish_points`/`flags` from `raw_data`. Re-check Task 1's implementation before starting this task: if `raw_data` truly isn't in `dashboard["agent_opinions"]`, this task's getter will have nothing to aggregate from. **Resolve this by amending Task 1's dict** (edit `src/agent/orchestrator.py` again) to also include `"raw_data": op.raw_data` in each serialized opinion dict, then update Task 1's test assertion accordingly (add `"raw_data": {}` — since the test's fake `AgentOpinion` objects don't set `raw_data`, it defaults to `{}` per the `AgentOpinion` dataclass's own default, confirm this default in `src/agent/protocols.py` before editing). Do this amendment as the first step of Task 2, not as a separate task, since it's a small, tightly-coupled correction to Task 1's own output shape discovered while implementing Task 2.

- [ ] **Step 1: Amend Task 1's capture step to include `raw_data`**

In `src/agent/orchestrator.py`, change the dict built in `run()` (from Task 1) to add `raw_data`:

```python
            dashboard["agent_opinions"] = [
                {
                    "agent_name": op.agent_name,
                    "signal": op.signal,
                    "confidence": op.confidence,
                    "reasoning": op.reasoning,
                    "raw_data": op.raw_data,
                }
                for op in ctx.opinions
            ]
```

Update `tests/test_multi_agent.py::test_run_serializes_ctx_opinions_into_dashboard_agent_opinions`'s assertion to include `"raw_data": {}` in each expected dict (matching `AgentOpinion`'s default `raw_data` value when not explicitly set — confirm this default is `{}` via `src/agent/protocols.py`'s `AgentOpinion` dataclass definition before writing the assertion; if the default is `None` instead, use `None` in the expected dicts).

Run: `python3 -m pytest tests/test_multi_agent.py -v -k test_run_serializes_ctx_opinions_into_dashboard_agent_opinions`
Expected: PASS after the amendment.

Commit this amendment separately before continuing:
```bash
git add src/agent/orchestrator.py tests/test_multi_agent.py
git commit -m "fix: include raw_data in dashboard[agent_opinions] for bull/bear aggregation"
```

- [ ] **Step 2: Write the failing tests for the two new getters**

Create `tests/test_analysis_result_agent_opinions.py`:

```python
# -*- coding: utf-8 -*-
"""Tests for AnalysisResult.get_agent_opinions() / get_bullish_bearish_points()."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.analyzer import AnalysisResult  # noqa: E402


def _make_result(dashboard):
    # AnalysisResult's only required fields (no default) are code/name/
    # sentiment_score/trend_prediction/operation_advice -- see the
    # @dataclass definition at src/analyzer.py:1669-1686. Everything else
    # defaults, so only those plus `dashboard` need to be set here.
    return AnalysisResult(
        code="600019",
        name="宝钢股份",
        sentiment_score=50,
        trend_prediction="test",
        operation_advice="hold",
        dashboard=dashboard,
    )


def test_get_agent_opinions_returns_dashboard_field_verbatim():
    dashboard = {
        "agent_opinions": [
            {"agent_name": "technical", "signal": "buy", "confidence": 0.72, "reasoning": "MA金叉", "raw_data": {}},
        ]
    }
    result = _make_result(dashboard)
    assert result.get_agent_opinions() == dashboard["agent_opinions"]


def test_get_agent_opinions_returns_empty_list_when_dashboard_missing():
    result = _make_result(None)
    assert result.get_agent_opinions() == []


def test_get_agent_opinions_returns_empty_list_when_key_absent():
    result = _make_result({"core_conclusion": {}})
    assert result.get_agent_opinions() == []


def test_get_bullish_bearish_points_aggregates_per_mapping_table():
    dashboard = {
        "agent_opinions": [
            {
                "agent_name": "intel", "signal": "hold", "confidence": 0.5, "reasoning": "",
                "raw_data": {
                    "positive_catalysts": ["行业复苏"],
                    "risk_alerts": ["股东减持"],
                },
            },
            {
                "agent_name": "macro_intel", "signal": "hold", "confidence": 0.6, "reasoning": "",
                "raw_data": {
                    "bullish_points": ["政策支持"],
                    "bearish_points": ["关税压力"],
                },
            },
            {
                "agent_name": "risk", "signal": "hold", "confidence": 0.4, "reasoning": "",
                "raw_data": {
                    "flags": [{"category": "valuation", "severity": "low", "description": "PB偏低"}],
                },
            },
            {
                "agent_name": "technical", "signal": "buy", "confidence": 0.8, "reasoning": "",
                "raw_data": {},
            },
        ]
    }
    result = _make_result(dashboard)
    points = result.get_bullish_bearish_points()

    assert points["bullish"] == [
        {"text": "行业复苏", "source_agent": "intel"},
        {"text": "政策支持", "source_agent": "macro_intel"},
    ]
    assert points["bearish"] == [
        {"text": "股东减持", "source_agent": "intel"},
        {"text": "关税压力", "source_agent": "macro_intel"},
        {"text": "PB偏低", "source_agent": "risk"},
    ]


def test_get_bullish_bearish_points_handles_missing_or_malformed_raw_data():
    dashboard = {
        "agent_opinions": [
            {"agent_name": "intel", "signal": "hold", "confidence": 0.5, "reasoning": ""},  # no raw_data key at all
            {"agent_name": "macro_intel", "signal": "hold", "confidence": 0.5, "reasoning": "", "raw_data": None},  # raw_data is None
            {"agent_name": "risk", "signal": "hold", "confidence": 0.5, "reasoning": "", "raw_data": {"flags": "not-a-list"}},  # malformed flags
        ]
    }
    result = _make_result(dashboard)
    points = result.get_bullish_bearish_points()
    assert points == {"bullish": [], "bearish": []}


def test_get_bullish_bearish_points_returns_empty_when_dashboard_missing():
    result = _make_result(None)
    assert result.get_bullish_bearish_points() == {"bullish": [], "bearish": []}
```

Before writing this test file, read `src/analyzer.py`'s `AnalysisResult` dataclass field list (starting line 1670) to confirm which fields are required vs have defaults — adjust `_make_result`'s constructor call in the test file to supply whatever fields are actually required (the fields listed above are a best guess at the required set based on common report fields; the actual dataclass may need more or fewer — match it exactly by reading the class definition first).

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_analysis_result_agent_opinions.py -v`
Expected: FAIL with `AttributeError: 'AnalysisResult' object has no attribute 'get_agent_opinions'`.

- [ ] **Step 4: Implement the two getters**

In `src/analyzer.py`, add after `get_risk_alerts` (line ~1813):

```python
    def get_agent_opinions(self) -> List[Dict[str, Any]]:
        """获取多 Agent 各自的意见（signal/confidence/reasoning/raw_data）"""
        if self.dashboard and 'agent_opinions' in self.dashboard:
            return self.dashboard.get('agent_opinions') or []
        return []

    def get_bullish_bearish_points(self) -> Dict[str, List[Dict[str, str]]]:
        """按固定映射表，从各 Agent 的 raw_data 里聚合看多/看空条目，每条带来源 Agent 标签"""
        bullish: List[Dict[str, str]] = []
        bearish: List[Dict[str, str]] = []

        for opinion in self.get_agent_opinions():
            agent_name = opinion.get('agent_name', '')
            raw_data = opinion.get('raw_data') or {}
            if not isinstance(raw_data, dict):
                continue

            if agent_name == 'intel':
                for text in raw_data.get('positive_catalysts') or []:
                    if isinstance(text, str) and text:
                        bullish.append({"text": text, "source_agent": agent_name})
                for text in raw_data.get('risk_alerts') or []:
                    if isinstance(text, str) and text:
                        bearish.append({"text": text, "source_agent": agent_name})
            elif agent_name == 'macro_intel':
                for text in raw_data.get('bullish_points') or []:
                    if isinstance(text, str) and text:
                        bullish.append({"text": text, "source_agent": agent_name})
                for text in raw_data.get('bearish_points') or []:
                    if isinstance(text, str) and text:
                        bearish.append({"text": text, "source_agent": agent_name})
            elif agent_name == 'risk':
                flags = raw_data.get('flags')
                if isinstance(flags, list):
                    for flag in flags:
                        if isinstance(flag, dict):
                            description = flag.get('description')
                            if isinstance(description, str) and description:
                                bearish.append({"text": description, "source_agent": agent_name})

        return {"bullish": bullish, "bearish": bearish}
```

Check the top of `src/analyzer.py` for existing `Dict`/`Any`/`List` imports from `typing` (used elsewhere in the file already, per `get_sniper_points`'s own `Dict[str, str]` return type annotation) — no new imports should be needed.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_analysis_result_agent_opinions.py -v`
Expected: all 7 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/analyzer.py tests/test_analysis_result_agent_opinions.py
git commit -m "feat: add AnalysisResult.get_agent_opinions/get_bullish_bearish_points"
```

---

### Task 3: Wire into `analysis_service.py`'s response builder

**Files:**
- Modify: `src/services/analysis_service.py:152-247` (`AnalysisService._build_analysis_response`, a bound method — confirmed exact signature: `def _build_analysis_response(self, result: Any, query_id: str, report_type: str = "detailed") -> Dict[str, Any]`, class `AnalysisService` starts at line 38)
- Test: create `tests/test_analysis_service_multi_agent_insights.py` (confirmed via `grep -rn "_build_analysis_response" tests/` that no existing test file covers this method directly)

**Interfaces:**
- Consumes: `result.get_agent_opinions()`, `result.get_bullish_bearish_points()` (Task 2, called defensively via `hasattr` matching this method's own existing style for `get_sniper_points` at line 171), `result.dashboard.get('signal_attribution')` (existing field, already on `self.dashboard`, just never extracted before).
- Produces (used by Tasks 4-6): `report["multi_agent_insights"]` — a dict with keys `opinions` (list, from `get_agent_opinions()`), `bullish_points`/`bearish_points` (lists, from `get_bullish_bearish_points()`), `signal_attribution` (dict, passed through from `dashboard.signal_attribution` as-is, or `None` if absent).

- [ ] **Step 1: Write the failing test**

Create `tests/test_analysis_service_multi_agent_insights.py`:

```python
# -*- coding: utf-8 -*-
"""Tests for AnalysisService._build_analysis_response's multi_agent_insights field."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.analyzer import AnalysisResult  # noqa: E402
from src.services.analysis_service import AnalysisService  # noqa: E402


def _make_result_with_dashboard(dashboard):
    # Same required-fields rationale as tests/test_analysis_result_agent_opinions.py:
    # code/name/sentiment_score/trend_prediction/operation_advice are the only
    # fields with no default on the AnalysisResult dataclass.
    return AnalysisResult(
        code="600019",
        name="宝钢股份",
        sentiment_score=50,
        trend_prediction="test",
        operation_advice="hold",
        dashboard=dashboard,
    )


def test_build_analysis_response_includes_multi_agent_insights():
    dashboard = {
        "signal_attribution": {
            "technical_indicators": 35, "news_sentiment": 20,
            "fundamentals": 25, "market_conditions": 20,
        },
        "agent_opinions": [
            {"agent_name": "technical", "signal": "buy", "confidence": 0.72, "reasoning": "MA金叉", "raw_data": {}},
            {
                "agent_name": "macro_intel", "signal": "hold", "confidence": 0.6, "reasoning": "",
                "raw_data": {"bullish_points": ["政策支持"], "bearish_points": ["关税压力"]},
            },
        ],
    }
    result = _make_result_with_dashboard(dashboard)

    service = AnalysisService()
    response = service._build_analysis_response(result, query_id="q-1", report_type="detailed")

    assert response["report"]["multi_agent_insights"] == {
        "opinions": dashboard["agent_opinions"],
        "bullish_points": [{"text": "政策支持", "source_agent": "macro_intel"}],
        "bearish_points": [{"text": "关税压力", "source_agent": "macro_intel"}],
        "signal_attribution": dashboard["signal_attribution"],
    }


def test_build_analysis_response_defaults_multi_agent_insights_when_dashboard_missing():
    result = _make_result_with_dashboard(None)

    service = AnalysisService()
    response = service._build_analysis_response(result, query_id="q-2", report_type="detailed")

    assert response["report"]["multi_agent_insights"] == {
        "opinions": [],
        "bullish_points": [],
        "bearish_points": [],
        "signal_attribution": None,
    }
```

If constructing `AnalysisService()` requires constructor arguments (check `src/services/analysis_service.py:45` `__init__` first — the plan's earlier research shows a bare `def __init__(self):` with no params, so this should work directly; if that's changed, adapt the test to whatever the real constructor now requires) or if `_build_analysis_response` raises on some other attribute access unrelated to this task's change (e.g. a global diagnostic-context singleton that isn't initialized in a bare test run), read the actual error and add the minimal real setup needed — do not paper over a real failure by mocking it away without understanding why it failed.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_analysis_service_multi_agent_insights.py -v`
Expected: FAIL — `KeyError: 'multi_agent_insights'`.

- [ ] **Step 3: Implement the response field**

In `src/services/analysis_service.py`, inside `_build_analysis_response`, add right after the existing `sniper_points` block (lines 170-172):

```python
        # 获取多 Agent 意见与看多看空聚合（缺失时优雅降级为空）
        agent_opinions = []
        if hasattr(result, 'get_agent_opinions'):
            agent_opinions = result.get_agent_opinions() or []
        bullish_bearish = {"bullish": [], "bearish": []}
        if hasattr(result, 'get_bullish_bearish_points'):
            bullish_bearish = result.get_bullish_bearish_points() or {"bullish": [], "bearish": []}
        signal_attribution = None
        if getattr(result, "dashboard", None):
            signal_attribution = result.dashboard.get("signal_attribution")
```

Then, inside the `report = {...}` dict construction (the one containing `"meta"`/`"summary"`/`"strategy"`/`"details"` keys, currently ending with the `"details": {...}` block at line 237-243), add a new sibling top-level key right after `"details": {...}`:

```python
            "multi_agent_insights": {
                "opinions": agent_opinions,
                "bullish_points": bullish_bearish.get("bullish", []),
                "bearish_points": bullish_bearish.get("bearish", []),
                "signal_attribution": signal_attribution,
            },
```

This must be placed before the closing `}` of the `report` dict, and before the subsequent `if hasattr(result, "to_dict"):` line (line 244) that follows the dict literal.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_analysis_service_multi_agent_insights.py -v`
Expected: both tests pass.

- [ ] **Step 5: Run the analysis API contract tests to confirm no regressions**

Run: `python3 -m pytest tests/test_analysis_api_contract.py -v`
Expected: all pass (this is a pure additive field, existing response consumers reading only their own known keys are unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/services/analysis_service.py tests/test_analysis_service_multi_agent_insights.py
git commit -m "feat: include multi_agent_insights in the analysis API response"
```

---

### Task 4: Frontend TypeScript types + i18n text

**Files:**
- Modify: `apps/dsa-web/src/types/analysis.ts` (near `ReportStrategy` interface, line ~116-121, and `AnalysisReport` interface, line ~368-373)
- Modify: `apps/dsa-web/src/utils/reportLanguage.ts` (all three `zh`/`en`/`ko` blocks inside `REPORT_TEXT`)

**Interfaces:**
- Consumes: nothing (pure type/text additions).
- Produces (used by Tasks 5-6): `MultiAgentOpinion`, `MultiAgentPoint`, `SignalAttribution`, `MultiAgentInsights` TypeScript interfaces; `AnalysisReport.multiAgentInsights?: MultiAgentInsights`; new i18n keys on `getReportText()`'s return object (used by the component in Task 5).

- [ ] **Step 1: Add the new TypeScript interfaces**

In `apps/dsa-web/src/types/analysis.ts`, add after the `ReportStrategy` interface (after line 121):

```typescript
export interface MultiAgentOpinion {
  agentName: string;
  signal: string;
  confidence: number;
  reasoning: string;
}

export interface MultiAgentPoint {
  text: string;
  sourceAgent: string;
}

export interface SignalAttribution {
  technicalIndicators?: number;
  newsSentiment?: number;
  fundamentals?: number;
  marketConditions?: number;
  strongestBullishSignal?: string;
  strongestBearishSignal?: string;
}

export interface MultiAgentInsights {
  opinions: MultiAgentOpinion[];
  bullishPoints: MultiAgentPoint[];
  bearishPoints: MultiAgentPoint[];
  signalAttribution?: SignalAttribution | null;
}
```

Then change the `AnalysisReport` interface:

```typescript
export interface AnalysisReport {
  meta: ReportMeta;
  summary: ReportSummary;
  strategy?: ReportStrategy;
  details?: ReportDetails;
}
```

to:

```typescript
export interface AnalysisReport {
  meta: ReportMeta;
  summary: ReportSummary;
  strategy?: ReportStrategy;
  details?: ReportDetails;
  multiAgentInsights?: MultiAgentInsights;
}
```

(Remember: the backend returns snake_case keys like `agent_name`/`source_agent`/`technical_indicators`, but the existing `toCamelCase()` transform in `apps/dsa-web/src/api/analysis.ts` (applied to the whole API response before it reaches components) converts these automatically — the TypeScript interfaces above are written in the POST-transform camelCase shape, matching every other interface in this file.)

- [ ] **Step 2: Add i18n text keys**

In `apps/dsa-web/src/utils/reportLanguage.ts`, add the following keys to **all three** language blocks (`zh`, `en`, `ko` — find each block's start via the line numbers noted in this plan's research, or grep for `idealBuy:` to locate all three occurrences quickly):

zh block, add:
```typescript
    multiAgentInsightsTitle: '多方观点',
    agentLabelTechnical: '技术面',
    agentLabelIntel: '个股消息面',
    agentLabelMacroIntel: '宏观政策',
    agentLabelRisk: '风险面',
    bullishPointsTitle: '看多',
    bearishPointsTitle: '看空',
    signalAttributionTitle: '权重占比',
```

en block, add:
```typescript
    multiAgentInsightsTitle: 'Multi-Agent Views',
    agentLabelTechnical: 'Technical',
    agentLabelIntel: 'Company Intel',
    agentLabelMacroIntel: 'Macro & Policy',
    agentLabelRisk: 'Risk',
    bullishPointsTitle: 'Bullish',
    bearishPointsTitle: 'Bearish',
    signalAttributionTitle: 'Signal Weighting',
```

ko block, add:
```typescript
    multiAgentInsightsTitle: '멀티 에이전트 의견',
    agentLabelTechnical: '기술적 분석',
    agentLabelIntel: '종목 뉴스',
    agentLabelMacroIntel: '거시/정책',
    agentLabelRisk: '리스크',
    bullishPointsTitle: '강세 요인',
    bearishPointsTitle: '약세 요인',
    signalAttributionTitle: '신호 가중치',
```

Add each block's new keys in the same style/indentation as the surrounding existing keys (e.g. right after `idealBuy:` in each block, or wherever fits the existing key grouping/ordering convention — match the file's existing formatting exactly).

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd apps/dsa-web && npx tsc --noEmit`
Expected: no new type errors (this task doesn't touch any component yet, so nothing should reference the new types incorrectly).

- [ ] **Step 4: Commit**

```bash
cd apps/dsa-web
git add src/types/analysis.ts src/utils/reportLanguage.ts
git commit -m "feat: add TypeScript types and i18n text for multi-agent insights"
```

---

### Task 5: `MultiAgentInsights.tsx` component

**Files:**
- Create: `apps/dsa-web/src/components/report/MultiAgentInsights.tsx`
- Test: `apps/dsa-web/src/components/report/__tests__/MultiAgentInsights.test.tsx`

**Interfaces:**
- Consumes: `MultiAgentInsights`/`MultiAgentOpinion`/`MultiAgentPoint`/`SignalAttribution` types (Task 4), `Card`/`DashboardPanelHeader` components (existing, `apps/dsa-web/src/components/common/Card.tsx` and `apps/dsa-web/src/components/dashboard/DashboardPanelHeader.tsx`), `getReportText`/`normalizeReportLanguage` (existing, `apps/dsa-web/src/utils/reportLanguage.ts`).
- Produces (used by Task 6): `MultiAgentInsights` React component, `export const MultiAgentInsights: React.FC<MultiAgentInsightsProps>`.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/dsa-web/src/components/report/__tests__/MultiAgentInsights.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MultiAgentInsights } from '../MultiAgentInsights';
import type { MultiAgentInsights as MultiAgentInsightsData } from '../../../types/analysis';

const baseInsights: MultiAgentInsightsData = {
  opinions: [
    { agentName: 'technical', signal: 'buy', confidence: 0.72, reasoning: 'MA金叉' },
    { agentName: 'macro_intel', signal: 'hold', confidence: 0.6, reasoning: '宏观缺乏强催化' },
  ],
  bullishPoints: [{ text: '政策支持', sourceAgent: 'macro_intel' }],
  bearishPoints: [{ text: '关税压力', sourceAgent: 'macro_intel' }],
  signalAttribution: {
    technicalIndicators: 35,
    newsSentiment: 20,
    fundamentals: 25,
    marketConditions: 20,
  },
};

describe('MultiAgentInsights', () => {
  it('renders agent opinions, bullish/bearish points, and signal attribution', () => {
    render(<MultiAgentInsights insights={baseInsights} language="zh" />);

    expect(screen.getByText('多方观点')).toBeInTheDocument();
    expect(screen.getByText('MA金叉')).toBeInTheDocument();
    expect(screen.getByText('政策支持')).toBeInTheDocument();
    expect(screen.getByText('关税压力')).toBeInTheDocument();
    expect(screen.getByText('看多')).toBeInTheDocument();
    expect(screen.getByText('看空')).toBeInTheDocument();
  });

  it('renders English labels when language is en', () => {
    render(<MultiAgentInsights insights={baseInsights} language="en" />);
    expect(screen.getByText('Multi-Agent Views')).toBeInTheDocument();
    expect(screen.getByText('Bullish')).toBeInTheDocument();
    expect(screen.getByText('Bearish')).toBeInTheDocument();
  });

  it('returns null when insights is undefined', () => {
    const { container } = render(<MultiAgentInsights insights={undefined} language="zh" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('returns null when opinions, bullishPoints, and bearishPoints are all empty', () => {
    const { container } = render(
      <MultiAgentInsights
        insights={{ opinions: [], bullishPoints: [], bearishPoints: [] }}
        language="zh"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders bullish column even when bearishPoints is empty, and vice versa', () => {
    render(
      <MultiAgentInsights
        insights={{ ...baseInsights, bearishPoints: [] }}
        language="zh"
      />,
    );
    expect(screen.getByText('政策支持')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/dsa-web && npx vitest run src/components/report/__tests__/MultiAgentInsights.test.tsx`
Expected: FAIL — module `../MultiAgentInsights` does not exist.

- [ ] **Step 3: Implement the component**

```tsx
// apps/dsa-web/src/components/report/MultiAgentInsights.tsx
import type React from 'react';
import type { MultiAgentInsights as MultiAgentInsightsData, ReportLanguage } from '../../types/analysis';
import { Card } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';

interface MultiAgentInsightsProps {
  insights?: MultiAgentInsightsData;
  language?: ReportLanguage;
}

const AGENT_LABEL_KEY: Record<string, 'agentLabelTechnical' | 'agentLabelIntel' | 'agentLabelMacroIntel' | 'agentLabelRisk'> = {
  technical: 'agentLabelTechnical',
  intel: 'agentLabelIntel',
  macro_intel: 'agentLabelMacroIntel',
  risk: 'agentLabelRisk',
};

const ATTRIBUTION_SEGMENTS: Array<{ key: keyof NonNullable<MultiAgentInsightsData['signalAttribution']>; labelKey: 'agentLabelTechnical' | 'agentLabelIntel' | 'agentLabelRisk'; color: string }> = [
  { key: 'technicalIndicators', labelKey: 'agentLabelTechnical', color: 'var(--home-strategy-buy)' },
  { key: 'newsSentiment', labelKey: 'agentLabelIntel', color: 'var(--home-strategy-secondary)' },
  { key: 'fundamentals', labelKey: 'agentLabelRisk', color: 'var(--home-strategy-tone, #999)' },
];

export const MultiAgentInsights: React.FC<MultiAgentInsightsProps> = ({ insights, language = 'zh' }) => {
  if (!insights) {
    return null;
  }

  const { opinions, bullishPoints, bearishPoints, signalAttribution } = insights;
  if (opinions.length === 0 && bullishPoints.length === 0 && bearishPoints.length === 0) {
    return null;
  }

  const reportLanguage = normalizeReportLanguage(language);
  const text = getReportText(reportLanguage);

  return (
    <Card>
      <DashboardPanelHeader title={text.multiAgentInsightsTitle} />

      {opinions.length > 0 && (
        <div className="flex flex-col gap-1 mb-4">
          {opinions.map((opinion) => {
            const labelKey = AGENT_LABEL_KEY[opinion.agentName];
            const label = labelKey ? text[labelKey] : opinion.agentName;
            return (
              <div key={opinion.agentName} className="flex items-center justify-between text-sm">
                <span className="text-muted-text">{label}</span>
                <span className="font-mono">
                  {opinion.signal} · {opinion.confidence.toFixed(2)}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {signalAttribution && (
        <div className="mb-4">
          <div className="text-xs text-muted-text mb-1">{text.signalAttributionTitle}</div>
          <div className="flex h-2 w-full overflow-hidden rounded-full">
            {[
              { value: signalAttribution.technicalIndicators, color: '#3b82f6' },
              { value: signalAttribution.newsSentiment, color: '#10b981' },
              { value: signalAttribution.fundamentals, color: '#f59e0b' },
              { value: signalAttribution.marketConditions, color: '#8b5cf6' },
            ].map((segment, idx) => (
              <div
                key={idx}
                style={{ width: `${segment.value ?? 0}%`, backgroundColor: segment.color }}
                title={`${segment.value ?? 0}%`}
              />
            ))}
          </div>
        </div>
      )}

      {(bullishPoints.length > 0 || bearishPoints.length > 0) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <div className="text-xs font-semibold text-green-600 mb-2">{text.bullishPointsTitle}</div>
            <ul className="list-disc list-inside space-y-1 text-sm">
              {bullishPoints.map((point, idx) => (
                <li key={idx}>{point.text}</li>
              ))}
            </ul>
          </div>
          <div>
            <div className="text-xs font-semibold text-red-600 mb-2">{text.bearishPointsTitle}</div>
            <ul className="list-disc list-inside space-y-1 text-sm">
              {bearishPoints.map((point, idx) => (
                <li key={idx}>{point.text}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Card>
  );
};
```

Before finalizing, check `apps/dsa-web/src/components/common/index.ts` and `apps/dsa-web/src/components/dashboard/index.ts` (or equivalent barrel files) to confirm `Card` and `DashboardPanelHeader` are actually exported from `'../common'`/`'../dashboard'` as imported above (matching `ReportStrategy.tsx`'s own import style) — adjust the import paths if the barrel file structure differs.

Note: the `ATTRIBUTION_SEGMENTS` constant declared above is unused in this implementation (the inline array in the JSX is used instead for simplicity) — remove the unused `ATTRIBUTION_SEGMENTS`/`AGENT_LABEL_KEY` declarations if your linter flags them, or wire `AGENT_LABEL_KEY` in for the attribution row's labels too if you want the bar segments individually labeled (optional polish, not required for the tests to pass).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/dsa-web && npx vitest run src/components/report/__tests__/MultiAgentInsights.test.tsx`
Expected: all 5 tests pass.

- [ ] **Step 5: Run lint**

Run: `cd apps/dsa-web && npm run lint`
Expected: no errors (fix any unused-variable lint errors from Step 3's note above if they occur).

- [ ] **Step 6: Commit**

```bash
cd apps/dsa-web
git add src/components/report/MultiAgentInsights.tsx src/components/report/__tests__/MultiAgentInsights.test.tsx
git commit -m "feat: add MultiAgentInsights report component"
```

---

### Task 6: Wire into `ReportSummary.tsx`

**Files:**
- Modify: `apps/dsa-web/src/components/report/ReportSummary.tsx`
- Test: extend `apps/dsa-web/src/components/report/__tests__/` (check for an existing `ReportSummary.test.tsx`; if none exists, a smoke test isn't required for this task since `MultiAgentInsights.tsx` already has its own full test coverage — this task is pure wiring)

**Interfaces:**
- Consumes: `MultiAgentInsights` component (Task 5), `report.multiAgentInsights` (Task 4's type, already flowing through the existing `toCamelCase()`-transformed API response by this point since Task 3 added it server-side).
- Produces: nothing further downstream — this is the final integration point.

- [ ] **Step 1: Add the import and destructure the new field**

In `apps/dsa-web/src/components/report/ReportSummary.tsx`, change:

```tsx
import { ReportStrategy } from './ReportStrategy';
```

to add a new import line right after it:

```tsx
import { ReportStrategy } from './ReportStrategy';
import { MultiAgentInsights } from './MultiAgentInsights';
```

Change:

```tsx
  const { meta, summary, strategy, details } = report;
```

to:

```tsx
  const { meta, summary, strategy, details, multiAgentInsights } = report;
```

- [ ] **Step 2: Insert the component into the render tree**

Change:

```tsx
      {/* 策略点位区 */}
      <ReportStrategy strategy={strategy} language={reportLanguage} />

      {/* 资讯区 */}
      <ReportNews recordId={recordId} limit={8} language={reportLanguage} />
```

to:

```tsx
      {/* 策略点位区 */}
      <ReportStrategy strategy={strategy} language={reportLanguage} />

      {/* 多方观点区 */}
      <MultiAgentInsights insights={multiAgentInsights} language={reportLanguage} />

      {/* 资讯区 */}
      <ReportNews recordId={recordId} limit={8} language={reportLanguage} />
```

- [ ] **Step 3: Verify TypeScript compiles and lint passes**

Run: `cd apps/dsa-web && npx tsc --noEmit && npm run lint`
Expected: no errors.

- [ ] **Step 4: Run the full frontend test suite to confirm no regressions**

Run: `cd apps/dsa-web && npm test`
Expected: all existing tests pass, plus `MultiAgentInsights.test.tsx`'s 5 tests.

- [ ] **Step 5: Commit**

```bash
cd apps/dsa-web
git add src/components/report/ReportSummary.tsx
git commit -m "feat: wire MultiAgentInsights into the report summary page"
```

---

### Task 7: Final verification (backend + frontend + real end-to-end)

**Files:** none (verification only)

- [ ] **Step 1: Backend syntax/test/lint check**

Run:
```bash
python3 -m py_compile src/agent/orchestrator.py src/analyzer.py src/services/analysis_service.py tests/test_multi_agent.py tests/test_analysis_result_agent_opinions.py
python3 -m pytest tests/test_multi_agent.py tests/test_analysis_result_agent_opinions.py tests/test_analysis_api_contract.py -v
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
```
Expected: all pass, flake8 `0`.

- [ ] **Step 2: Frontend build/lint/test check**

Run:
```bash
cd apps/dsa-web
npm run lint
npm run build
npm test
```
Expected: all pass, build succeeds with no TypeScript errors.

- [ ] **Step 3: Real end-to-end verification**

Using the already-running `newsgrab` services (collector-service on `http://127.0.0.1:18101`, confirmed live in this session) and a working LLM model (confirmed earlier in this session: `deepseek/DeepSeek-V4-Pro/2e01d` via the configured proxy, NOT `9de40` which is currently unavailable), run a real `full`-mode analysis for `600019`/`宝钢股份` via `build_agent_executor()` (same pattern as the earlier live test script in this session), and inspect the actual returned `AgentResult.dashboard["agent_opinions"]` to confirm:
- It's a non-empty list with `agent_name` values matching whichever agents actually ran (`technical`, `intel`, `macro_intel`, `risk` for `full` mode).
- Each entry has real `signal`/`confidence`/`reasoning` text (not empty/placeholder).

Then call `AnalysisResult.get_agent_opinions()`/`get_bullish_bearish_points()` (or trace through `_build_analysis_response` if that's easier to invoke standalone) against this same real dashboard to confirm the aggregation produces genuinely correct output for this real run's actual data (e.g. `macro_intel`'s real `bullish_points`/`bearish_points` from that run actually appear correctly tagged with `source_agent: "macro_intel"`).

- [ ] **Step 4: Report final status**

Summarize: all test counts and pass/fail status for both backend and frontend, confirmation that `npm run build` produced a clean production bundle, and the real end-to-end `agent_opinions`/bull-bear aggregation output from Step 3 (paste the actual JSON, not a description). Explicitly note: this plan's verification does NOT include an actual browser screenshot/visual render of the deployed page (no browser-automation tool is available in this environment) — component-level rendering correctness is proven by `MultiAgentInsights.test.tsx`'s Testing-Library assertions (which render to a real DOM), and data correctness is proven by the real end-to-end run in Step 3, but a pixel-level visual check of the live page has not been performed. If the user wants that, it would need to happen in an environment with browser access.
