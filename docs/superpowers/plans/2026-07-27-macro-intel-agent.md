# MacroIntelAgent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `MacroIntelAgent` to the `full`-mode multi-agent pipeline that searches Google News across 6 fixed regions (CN/JP/KR/SG/US/EU) for national policy, industry-chain, and bull/bear angles, feeding a standard `AgentOpinion` into the existing `DecisionAgent` weighting/disagreement machinery.

**Architecture:** A new `BaseAgent` subclass (`src/agent/agents/macro_intel_agent.py`), modeled directly on the existing `IntelAgent`, gets a dedicated tool (`search_macro_news`) that calls `collect_google_news()` with a per-request `language`/`region` override (already supported server-side by `newsgrab`'s `collector-service`, commit `66c88d5`). The agent is inserted into `full`-mode's chain only; `DecisionAgent`'s weighting guideline text is updated from a 3-way to a 4-way split. No new execution engine — reuses the existing generic ReAct tool-calling loop (`run_agent_loop`) that every `BaseAgent` subclass already gets for free.

**Tech Stack:** Python, existing `src/agent/` framework, `requests` (via the already-built `google_news_collector_client.py`).

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-07-27-macro-intel-agent-design.md`:

- **Only runs in `full` mode.** `quick`/`standard`/`specialist` modes are unaffected.
- **6 fixed regions, one call per region**: `CN→(zh-CN,CN)`, `JP→(ja,JP)`, `KR→(ko,KR)`, `SG→(en,SG)`, `US→(en,US)`, `EU→(en,GB)`.
- **`max_steps = 6`** — the agent does NOT need to cover all 6 regions every time; it prioritizes relevance within its budget.
- **Does NOT persist to `NewsIntel`** — `search_macro_news` is a free-text, multi-region query, not scoped to a single `stock_code`; unlike `search_google_news`, it never calls `_persist_news_response`.
- **`AgentOpinion` output shape**: `signal` (`strong_buy|buy|hold|sell|strong_sell`), `confidence` (0.0-1.0), `reasoning`, `raw_data` — same shape `IntelAgent`/`RiskAgent` already produce; consumed automatically by the existing `disagreement.py` and `DecisionAgent` machinery, no changes needed there.
- **`DecisionAgent` weighting**: Technical ~35%, Intel ~20%, Macro ~20%, Risk ~25% (risk override unchanged).
- **Never breaks the rest of the pipeline**: a `MacroIntelAgent` failure (JSON parse failure, tool errors) results in `post_process` returning `None` — same degrade-gracefully contract as `IntelAgent`/`RiskAgent` today.
- **Does NOT change** `IntelAgent`, `search_comprehensive_intel`, `search_google_news`, `disagreement.py`, `risk_override.py`, or `SkillAggregator`.
- Commit messages in English, no `Co-Authored-By` trailer (repo convention). New config must be reflected in `.env.example`; user-visible capability changes reflected in `docs/CHANGELOG.md`'s `[Unreleased]` (flat `- [类型] 描述` format).

---

### Task 1: `collect_google_news()` gains optional `language`/`region` override

**Files:**
- Modify: `src/services/google_news_collector_client.py` (`collect_google_news` signature at line 73-78, `_submit_job` at line 50-61)
- Test: `tests/test_google_news_collector_client.py`

**Interfaces:**
- Consumes: nothing new — this extends the already-existing `collect_google_news`.
- Produces (used by Task 2): `collect_google_news(query, max_results=5, days=7, timeout_sec=None, language=None, region=None) -> SearchResponse` — the two new trailing optional params, both `None` by default (omitted from the job request body when `None`, matching newsgrab's `collector-service` contract from `docs/superpowers/specs/2026-07-27-per-request-language-region.md`: absent = use deployment default).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_google_news_collector_client.py` (inside `CollectGoogleNewsTest`, after `test_successful_job_returns_articles_with_snippet_truncated`):

```python
    def test_language_region_included_in_job_request_when_given(self):
        from src.services import google_news_collector_client as client

        submit_resp = _mock_response({"job_id": "job-lang"}, status_code=201)
        poll_resp = _mock_response({
            "job_id": "job-lang", "status": "done", "result": [], "error": None,
        })

        with patch(
            "src.services.google_news_collector_client.requests.post",
            return_value=submit_resp,
        ) as mock_post, patch(
            "src.services.google_news_collector_client.requests.get",
            return_value=poll_resp,
        ), patch(
            "src.services.google_news_collector_client.time.sleep",
            return_value=None,
        ):
            client.collect_google_news("鉄鋼業界", language="ja", region="JP")

        _, kwargs = mock_post.call_args
        assert kwargs["json"]["params"]["language"] == "ja"
        assert kwargs["json"]["params"]["region"] == "JP"

    def test_language_region_omitted_from_job_request_when_not_given(self):
        from src.services import google_news_collector_client as client

        submit_resp = _mock_response({"job_id": "job-nolang"}, status_code=201)
        poll_resp = _mock_response({
            "job_id": "job-nolang", "status": "done", "result": [], "error": None,
        })

        with patch(
            "src.services.google_news_collector_client.requests.post",
            return_value=submit_resp,
        ) as mock_post, patch(
            "src.services.google_news_collector_client.requests.get",
            return_value=poll_resp,
        ), patch(
            "src.services.google_news_collector_client.time.sleep",
            return_value=None,
        ):
            client.collect_google_news("贵州茅台 600519")

        _, kwargs = mock_post.call_args
        assert "language" not in kwargs["json"]["params"]
        assert "region" not in kwargs["json"]["params"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_google_news_collector_client.py -v -k language_region`
Expected: FAIL — `test_language_region_included_in_job_request_when_given` fails with `TypeError: collect_google_news() got an unexpected keyword argument 'language'`.

- [ ] **Step 3: Implement the signature/body change**

In `src/services/google_news_collector_client.py`, change `_submit_job`:

```python
def _submit_job(base_url: str, query: str, max_results: int, days: int) -> str:
    response = requests.post(
        urljoin(base_url + "/", "jobs"),
        json={
            "backend": "google_news",
            "query": query,
            "params": {"max_results": max_results, "days": days},
        },
        timeout=_REQUEST_TIMEOUT_SEC,
    )
    response.raise_for_status()
    return response.json()["job_id"]
```

to:

```python
def _submit_job(
    base_url: str, query: str, max_results: int, days: int,
    language: Optional[str] = None, region: Optional[str] = None,
) -> str:
    params = {"max_results": max_results, "days": days}
    if language is not None:
        params["language"] = language
    if region is not None:
        params["region"] = region
    response = requests.post(
        urljoin(base_url + "/", "jobs"),
        json={"backend": "google_news", "query": query, "params": params},
        timeout=_REQUEST_TIMEOUT_SEC,
    )
    response.raise_for_status()
    return response.json()["job_id"]
```

and change `collect_google_news`'s signature and its call to `_submit_job`:

```python
def collect_google_news(
    query: str,
    max_results: int = 5,
    days: int = 7,
    timeout_sec: Optional[float] = None,
) -> SearchResponse:
```

to:

```python
def collect_google_news(
    query: str,
    max_results: int = 5,
    days: int = 7,
    timeout_sec: Optional[float] = None,
    language: Optional[str] = None,
    region: Optional[str] = None,
) -> SearchResponse:
```

and change the line `job_id = _submit_job(base_url, query, max_results, days)` to:

```python
        job_id = _submit_job(base_url, query, max_results, days, language=language, region=region)
```

Update the module/function docstring's final sentence to add: `"language`/`region`, when given, override the collector-service deployment's default for this call only (see newsgrab's per-request language/region support)."`

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_google_news_collector_client.py -v`
Expected: all pass (10 total: 8 existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/services/google_news_collector_client.py tests/test_google_news_collector_client.py
git commit -m "feat: support per-request language/region in collect_google_news"
```

---

### Task 2: `search_macro_news` agent tool

**Files:**
- Modify: `src/agent/tools/search_tools.py` (module docstring, append new tool + extend `ALL_SEARCH_TOOLS`)
- Test: `tests/test_search_macro_news_tool.py`

**Interfaces:**
- Consumes: `google_news_collector_client.is_enabled()`, `collect_google_news(query, max_results=5, days=7, language=.., region=..) -> SearchResponse` (Task 1).
- Produces (used by Task 3): `search_macro_news_tool` (a `ToolDefinition` named `"search_macro_news"`), registered in `ALL_SEARCH_TOOLS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_search_macro_news_tool.py
# -*- coding: utf-8 -*-
"""Tests for the search_macro_news Agent tool."""

import unittest
from unittest.mock import MagicMock, patch

from src.search_service import SearchResponse, SearchResult


def _response(query: str, *, success: bool = True) -> SearchResponse:
    return SearchResponse(
        query=query,
        provider="GoogleNews",
        success=success,
        error_message=None if success else "collection failed",
        results=[
            SearchResult(
                title="钢铁行业新闻标题",
                snippet="正文摘要",
                url="https://real-site.example/article",
                source="real-site.example",
                published_date="2026-07-27",
            )
        ] if success else [],
    )


class SearchMacroNewsToolTest(unittest.TestCase):
    def test_unknown_region_returns_error_without_calling_collector(self) -> None:
        from src.agent.tools.search_tools import _handle_search_macro_news

        with patch(
            "src.services.google_news_collector_client.is_enabled"
        ) as mock_enabled:
            result = _handle_search_macro_news("钢铁行业 产能政策", "FR")

        self.assertIn("error", result)
        self.assertIn("FR", result["error"])
        mock_enabled.assert_not_called()

    def test_returns_disabled_message_when_not_enabled(self) -> None:
        from src.agent.tools.search_tools import _handle_search_macro_news

        with patch(
            "src.services.google_news_collector_client.is_enabled", return_value=False
        ):
            result = _handle_search_macro_news("钢铁行业 产能政策", "CN")

        self.assertIn("error", result)
        self.assertIn("disabled", result["error"].lower())

    def test_returns_results_with_language_region_mapped_when_enabled(self) -> None:
        from src.agent.tools.search_tools import _handle_search_macro_news

        response = _response("钢铁行业 产能政策")

        with patch(
            "src.services.google_news_collector_client.is_enabled", return_value=True
        ), patch(
            "src.services.google_news_collector_client.collect_google_news",
            return_value=response,
        ) as mock_collect:
            result = _handle_search_macro_news("钢铁行业 产能政策", "JP")

        self.assertTrue(result["success"])
        self.assertEqual(result["region"], "JP")
        self.assertEqual(result["results_count"], 1)
        mock_collect.assert_called_once_with(
            "钢铁行业 产能政策", max_results=5, days=7, language="ja", region="JP"
        )

    def test_returns_failure_without_raising_on_collection_failure(self) -> None:
        from src.agent.tools.search_tools import _handle_search_macro_news

        response = _response("钢铁行业 产能政策", success=False)

        with patch(
            "src.services.google_news_collector_client.is_enabled", return_value=True
        ), patch(
            "src.services.google_news_collector_client.collect_google_news",
            return_value=response,
        ):
            result = _handle_search_macro_news("钢铁行业 产能政策", "CN")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "collection failed")

    def test_unexpected_exception_is_caught_and_returned_as_error(self) -> None:
        from src.agent.tools.search_tools import _handle_search_macro_news

        with patch(
            "src.services.google_news_collector_client.is_enabled", return_value=True
        ), patch(
            "src.services.google_news_collector_client.collect_google_news",
            side_effect=RuntimeError("boom"),
        ):
            result = _handle_search_macro_news("钢铁行业 产能政策", "US")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "boom")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_search_macro_news_tool.py -v`
Expected: FAIL with `ImportError: cannot import name '_handle_search_macro_news'`.

- [ ] **Step 3: Implement the handler and `ToolDefinition`**

Update the module docstring at the top of `src/agent/tools/search_tools.py` (currently ends with the `search_google_news` bullet) — append:

```python
- search_macro_news: multi-region (CN/JP/KR/SG/US/EU) free-text Google News
  search for national policy/industry-chain/bullish/bearish angles, via the
  same collector-service. Not scoped to a stock_code, does not persist.
"""
```

Append at the end of `src/agent/tools/search_tools.py`, after the existing `search_google_news_tool` definition and before the final `ALL_SEARCH_TOOLS = [...]` block, replacing:

```python
ALL_SEARCH_TOOLS = [
    search_stock_news_tool,
    search_comprehensive_intel_tool,
    search_google_news_tool,
]
```

with:

```python
# ============================================================
# search_macro_news
# ============================================================

_MACRO_REGION_LOCALE = {
    "CN": ("zh-CN", "CN"),
    "JP": ("ja", "JP"),
    "KR": ("ko", "KR"),
    "SG": ("en", "SG"),
    "US": ("en", "US"),
    "EU": ("en", "GB"),
}


def _handle_search_macro_news(query: str, region: str) -> dict:
    """Search Google News for a free-text macro/policy/industry-chain query
    in one of 6 fixed regions. Unlike search_google_news, this takes a
    free-text query (not stock_code/stock_name) and does NOT persist to
    NewsIntel -- it's not scoped to a single stock."""
    if region not in _MACRO_REGION_LOCALE:
        return {"error": f"unknown region {region!r}, must be one of {sorted(_MACRO_REGION_LOCALE)}"}

    from src.services import google_news_collector_client

    if not google_news_collector_client.is_enabled():
        return {
            "error": "Google News collector is disabled "
                     "(set GOOGLE_NEWS_COLLECTOR_URL to enable)"
        }

    language, country = _MACRO_REGION_LOCALE[region]
    try:
        response = google_news_collector_client.collect_google_news(
            query, max_results=5, days=7, language=language, region=country
        )
    except Exception as exc:
        logger.warning("Macro news collection failed for %r/%s: %s", query, region, exc)
        return {"query": query, "region": region, "success": False, "error": str(exc)}

    if not response.success:
        return {
            "query": response.query, "region": region,
            "success": False, "error": response.error_message,
        }

    return {
        "query": response.query,
        "region": region,
        "success": True,
        "results_count": len(response.results),
        "results": [
            {"title": r.title, "snippet": r.snippet, "url": r.url,
             "source": r.source, "published_date": r.published_date}
            for r in response.results
        ],
    }


search_macro_news_tool = ToolDefinition(
    name="search_macro_news",
    description="Search Google News for a free-text query (national policy, "
                "industry chain, bullish/bearish angle, etc.) in one of 6 fixed "
                "regions: CN, JP, KR, SG, US, EU. Slower than other search tools "
                "(may take up to ~40s per call) -- budget your calls carefully. "
                "Does NOT persist results (not scoped to a single stock).",
    parameters=[
        ToolParameter(name="query", type="string", description="Free-text search query, e.g. '钢铁行业 产能政策'"),
        ToolParameter(name="region", type="string", description="One of: CN, JP, KR, SG, US, EU",
                      enum=["CN", "JP", "KR", "SG", "US", "EU"]),
    ],
    handler=_handle_search_macro_news,
    category="search",
    policy=_NEWS_READ_POLICY,
)


ALL_SEARCH_TOOLS = [
    search_stock_news_tool,
    search_comprehensive_intel_tool,
    search_google_news_tool,
    search_macro_news_tool,
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_search_macro_news_tool.py -v`
Expected: all 5 pass.

- [ ] **Step 5: Run existing agent registry test to confirm no regressions**

Run: `python3 -m pytest tests/test_agent_registry.py -v`
Expected: all pass — `ALL_SEARCH_TOOLS` assertions use `assertGreater(len(...), 0)`, not an exact count, so a 4th tool doesn't break them.

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools/search_tools.py tests/test_search_macro_news_tool.py
git commit -m "feat: add search_macro_news agent tool for multi-region macro/policy search"
```

---

### Task 3: `MacroIntelAgent`

**Files:**
- Create: `src/agent/agents/macro_intel_agent.py`
- Test: append to `tests/test_multi_agent.py` (after the existing `TestIntelAgentPostProcess` class at line ~904-927, before `TestOrchestratorModes` at line 934)

**Interfaces:**
- Consumes: `search_macro_news_tool` (Task 2, registered by name `"search_macro_news"` in the tool registry), `BaseAgent`/`AgentContext`/`AgentOpinion` (`src/agent/agents/base_agent.py`, `src/agent/protocols.py` — unchanged, existing), `try_parse_json` (`src/agent/runner.py` — unchanged, existing).
- Produces (used by Task 4): `MacroIntelAgent` class with `agent_name = "macro_intel"`, `max_steps = 6`, `tool_names = ["search_macro_news"]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_multi_agent.py`, immediately after the existing `TestIntelAgentPostProcess` class (after line 927, before the `# ===` comment block preceding `TestOrchestratorModes`):

```python
class TestMacroIntelAgentPostProcess(unittest.TestCase):
    """Test MacroIntelAgent JSON parsing and opinion construction."""

    def test_parses_json_into_agent_opinion(self):
        from src.agent.agents.macro_intel_agent import MacroIntelAgent

        agent = MacroIntelAgent(tool_registry=MagicMock(), llm_adapter=MagicMock())
        ctx = AgentContext(query="test", stock_code="600019")
        raw = """```json
        {
          "signal": "buy",
          "confidence": 0.65,
          "reasoning": "中日韩产业链政策整体偏正面，未见重大风险",
          "regions_covered": ["CN", "JP", "KR"],
          "policy_notes": ["中国钢铁行业去产能政策延续"],
          "industry_chain_notes": ["日韩上游原材料价格企稳"],
          "bullish_points": ["政策支持"],
          "bearish_points": []
        }
        ```"""

        opinion = agent.post_process(ctx, raw)

        self.assertIsNotNone(opinion)
        self.assertEqual(opinion.agent_name, "macro_intel")
        self.assertEqual(opinion.signal, "buy")
        self.assertAlmostEqual(opinion.confidence, 0.65)
        self.assertEqual(ctx.get_data("macro_intel_opinion")["regions_covered"], ["CN", "JP", "KR"])

    def test_returns_none_on_unparseable_json(self):
        from src.agent.agents.macro_intel_agent import MacroIntelAgent

        agent = MacroIntelAgent(tool_registry=MagicMock(), llm_adapter=MagicMock())
        ctx = AgentContext(query="test", stock_code="600019")

        opinion = agent.post_process(ctx, "not valid json at all")

        self.assertIsNone(opinion)

    def test_agent_configuration(self):
        from src.agent.agents.macro_intel_agent import MacroIntelAgent

        agent = MacroIntelAgent(tool_registry=MagicMock(), llm_adapter=MagicMock())

        self.assertEqual(agent.agent_name, "macro_intel")
        self.assertEqual(agent.max_steps, 6)
        self.assertEqual(agent.tool_names, ["search_macro_news"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_multi_agent.py -v -k MacroIntelAgent`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agent.agents.macro_intel_agent'`.

- [ ] **Step 3: Implement `MacroIntelAgent`**

```python
# src/agent/agents/macro_intel_agent.py
# -*- coding: utf-8 -*-
"""
MacroIntelAgent — multi-region macro/policy/industry-chain intelligence specialist.

Responsible for:
- Searching national policy and industry-chain (upstream/downstream) news
  across 6 fixed regions: CN, JP, KR, SG, US, EU
- Considering both bullish and bearish angles, not just company-level news
- Producing a structured opinion that feeds into the same weighting/
  disagreement machinery as every other agent -- no new fusion logic needed
"""

from __future__ import annotations

import logging
from typing import Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.runner import try_parse_json

logger = logging.getLogger(__name__)


class MacroIntelAgent(BaseAgent):
    agent_name = "macro_intel"
    max_steps = 6
    tool_names = ["search_macro_news"]

    def system_prompt(self, ctx: AgentContext) -> str:
        return """\
You are a **Macro & Policy Intelligence Agent**. Your job is to search for \
broader market-moving context beyond company-specific news: national policy, \
industry-chain (upstream/downstream supply chain) dynamics, and both bullish \
and bearish angles -- across multiple regions, since a stock's industry can \
be moved by policy or supply-chain news from other markets.

## Available regions
CN, JP, KR, SG, US, EU -- each search_macro_news call targets exactly one.

## Budget
You have at most 6 tool calls. Do NOT try to cover all 6 regions x all \
angles -- prioritize the region/angle combinations most relevant to this \
stock's industry and listing market. E.g. a steel company likely benefits \
most from CN/JP/KR industry-chain and policy searches; a US tech stock from \
US/EU policy and bearish-angle searches. Skipping irrelevant regions \
entirely is expected and correct.

## Angles to consider (compose your own free-text query per call)
- National policy affecting this stock's industry
- Upstream/downstream supply chain dynamics
- Bullish catalysts (positive angle)
- Bearish risks (negative angle)

## Output Format
Return **only** a JSON object:
{
  "signal": "strong_buy|buy|hold|sell|strong_sell",
  "confidence": 0.0-1.0,
  "reasoning": "2-3 sentence summary synthesizing what was found across regions/angles",
  "regions_covered": ["CN", "JP", ...],
  "policy_notes": ["..."],
  "industry_chain_notes": ["..."],
  "bullish_points": ["..."],
  "bearish_points": ["..."]
}
"""

    def build_user_message(self, ctx: AgentContext) -> str:
        parts = [f"Assess macro/policy/industry-chain context for **{ctx.stock_code}**"]
        if ctx.stock_name:
            parts[0] += f" ({ctx.stock_name})"
        parts.append(
            "Decide which 1-6 region/angle combinations are most relevant to this "
            "stock's industry, call search_macro_news for each, then synthesize "
            "one JSON opinion covering what you actually found."
        )
        return "\n".join(parts)

    def post_process(self, ctx: AgentContext, raw_text: str) -> Optional[AgentOpinion]:
        parsed = try_parse_json(raw_text)
        if parsed is None:
            logger.warning("[MacroIntelAgent] failed to parse opinion JSON")
            return None

        ctx.set_data("macro_intel_opinion", parsed)

        return AgentOpinion(
            agent_name=self.agent_name,
            signal=parsed.get("signal", "hold"),
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=parsed.get("reasoning", ""),
            raw_data=parsed,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_multi_agent.py -v -k MacroIntelAgent`
Expected: all 3 pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent/agents/macro_intel_agent.py tests/test_multi_agent.py
git commit -m "feat: add MacroIntelAgent for multi-region macro/policy intelligence"
```

---

### Task 4: Wire into orchestrator, config, and DecisionAgent weighting

**Files:**
- Modify: `src/agent/orchestrator.py` (`_build_agent_chain` at line 765-797, `_get_sub_agent_timeout_map` at line 149-166)
- Modify: `src/config.py` (new field near line 855, new `parse_env_float` call near line 1774)
- Modify: `src/agent/agents/decision_agent.py` (weighting guidelines at line 81-85)
- Modify: `.env.example`
- Modify: `docs/CHANGELOG.md`
- Test: `tests/test_multi_agent.py` (`TestOrchestratorModes.test_full_mode` at line 961-966)

**Interfaces:**
- Consumes: `MacroIntelAgent` (Task 3).
- Produces: nothing further downstream — this is the final wiring task.

- [ ] **Step 1: Update the failing test for `full` mode's chain**

In `tests/test_multi_agent.py`, change:

```python
    def test_full_mode(self):
        orch = self._make_orchestrator("full")
        ctx = AgentContext(query="test", stock_code="600519")
        chain = orch._build_agent_chain(ctx)
        names = [a.agent_name for a in chain]
        self.assertEqual(names, ["technical", "intel", "risk", "decision"])
```

to:

```python
    def test_full_mode(self):
        orch = self._make_orchestrator("full")
        ctx = AgentContext(query="test", stock_code="600519")
        chain = orch._build_agent_chain(ctx)
        names = [a.agent_name for a in chain]
        self.assertEqual(names, ["technical", "intel", "macro_intel", "risk", "decision"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_multi_agent.py -v -k test_full_mode`
Expected: FAIL — actual chain is still `["technical", "intel", "risk", "decision"]`.

- [ ] **Step 3: Wire `MacroIntelAgent` into `_build_agent_chain`**

In `src/agent/orchestrator.py`, change:

```python
    def _build_agent_chain(self, ctx: AgentContext) -> list:
        """Instantiate the ordered agent list based on ``self.mode``."""
        from src.agent.agents.technical_agent import TechnicalAgent
        from src.agent.agents.intel_agent import IntelAgent
        from src.agent.agents.decision_agent import DecisionAgent
        from src.agent.agents.risk_agent import RiskAgent

        self._skill_agent_names = set()

        common_kwargs = dict(
            tool_registry=self.tool_registry,
            llm_adapter=self.llm_adapter,
            skill_instructions=self.skill_instructions,
            technical_skill_policy=self.technical_skill_policy,
        )

        technical = self._prepare_agent(TechnicalAgent(**common_kwargs))
        intel = self._prepare_agent(IntelAgent(**common_kwargs))
        risk = self._prepare_agent(RiskAgent(**common_kwargs))
        decision = self._prepare_agent(DecisionAgent(**common_kwargs))

        if self.mode == "quick":
            return [technical, decision]
        elif self.mode == "standard":
            return [technical, intel, decision]
        elif self.mode == "full":
            return [technical, intel, risk, decision]
        elif self.mode == "specialist":
            # Specialist agents are inserted lazily right before the decision
            # stage so the router can see the finished technical opinion.
            return [technical, intel, risk, decision]
        else:
            return [technical, intel, decision]
```

to:

```python
    def _build_agent_chain(self, ctx: AgentContext) -> list:
        """Instantiate the ordered agent list based on ``self.mode``."""
        from src.agent.agents.technical_agent import TechnicalAgent
        from src.agent.agents.intel_agent import IntelAgent
        from src.agent.agents.macro_intel_agent import MacroIntelAgent
        from src.agent.agents.decision_agent import DecisionAgent
        from src.agent.agents.risk_agent import RiskAgent

        self._skill_agent_names = set()

        common_kwargs = dict(
            tool_registry=self.tool_registry,
            llm_adapter=self.llm_adapter,
            skill_instructions=self.skill_instructions,
            technical_skill_policy=self.technical_skill_policy,
        )

        technical = self._prepare_agent(TechnicalAgent(**common_kwargs))
        intel = self._prepare_agent(IntelAgent(**common_kwargs))
        macro_intel = self._prepare_agent(MacroIntelAgent(**common_kwargs))
        risk = self._prepare_agent(RiskAgent(**common_kwargs))
        decision = self._prepare_agent(DecisionAgent(**common_kwargs))

        if self.mode == "quick":
            return [technical, decision]
        elif self.mode == "standard":
            return [technical, intel, decision]
        elif self.mode == "full":
            return [technical, intel, macro_intel, risk, decision]
        elif self.mode == "specialist":
            # Specialist agents are inserted lazily right before the decision
            # stage so the router can see the finished technical opinion.
            return [technical, intel, risk, decision]
        else:
            return [technical, intel, decision]
```

Note: `specialist` mode deliberately keeps its existing chain (`[technical, intel, risk, decision]`, no `macro_intel`) — the design spec/plan's Global Constraints say `MacroIntelAgent` only runs in `full` mode.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_multi_agent.py -v -k "test_quick_mode or test_standard_mode or test_full_mode or test_invalid_mode"`
Expected: all 4 pass (confirms `quick`/`standard` unaffected, `full` now includes `macro_intel`).

- [ ] **Step 5: Add the timeout config field**

In `src/config.py`, add a new field right after `agent_risk_agent_timeout_s: float = 0` (around line 855):

```python
    agent_macro_intel_agent_timeout_s: float = 0
```

Add the corresponding parsing call right after the `agent_risk_agent_timeout_s=parse_env_float(...)` block (around line 1777):

```python
            agent_macro_intel_agent_timeout_s=parse_env_float(
                os.getenv('AGENT_MACRO_INTEL_AGENT_TIMEOUT_S'), 0,
                field_name='AGENT_MACRO_INTEL_AGENT_TIMEOUT_S', minimum=0,
            ),
```

In `src/agent/orchestrator.py::_get_sub_agent_timeout_map`, add an entry to the `entries` list right after `("risk", "agent_risk_agent_timeout_s")`:

```python
            ("macro_intel", "agent_macro_intel_agent_timeout_s"),
```

- [ ] **Step 6: Run the full orchestrator test file to confirm no regressions**

Run: `python3 -m pytest tests/test_multi_agent.py -v`
Expected: all tests pass (including the pre-existing `test_chain_agents_inherit_orchestrator_max_steps` and any timeout-map tests — check output for any failure and fix before proceeding, this file covers a lot of orchestrator behavior that must stay green).

- [ ] **Step 7: Update `DecisionAgent`'s weighting guideline text**

In `src/agent/agents/decision_agent.py`, change:

```python
## Signal Weighting Guidelines
- Technical opinion weight: ~40%
- Intel / sentiment weight: ~30%
- Risk flags weight: ~30% (negative override: any high-severity risk caps signal at "hold")
- If a skill opinion is present, blend it at 20% weight (reducing others proportionally)
```

to:

```python
## Signal Weighting Guidelines
- Technical opinion weight: ~35%
- Intel / sentiment weight (company news, capital flow): ~20%
- Macro / policy weight (national policy, industry chain, multi-region): ~20%
- Risk flags weight: ~25% (negative override: any high-severity risk caps signal at "hold")
- If a skill opinion is present, blend it at 20% weight (reducing others proportionally)
```

- [ ] **Step 8: Add `.env.example` entry**

Find the existing agent-timeout section in `.env.example` (search for `AGENT_RISK_AGENT_TIMEOUT_S` or `AGENT_INTEL_AGENT_TIMEOUT_S` — add the new line in the same block, immediately after the risk-agent timeout line):

```bash
# MacroIntelAgent 单独超时（秒），0=不设专属上限（只受全局 orchestrator 超时约束）。
# search_macro_news 单次调用可能到 40 秒、预算最多 6 步，如果要设专属上限，
# 建议给比其它 Agent 更宽松的值（如 180-300）。
# AGENT_MACRO_INTEL_AGENT_TIMEOUT_S=0
```

- [ ] **Step 9: Add the changelog entry**

In `docs/CHANGELOG.md`, add a new line as the first bullet under `## [Unreleased]`:

```markdown
- [新功能] 新增 `MacroIntelAgent`，在 `full` 模式的多 Agent 分析流程中新增一个多地区（中日韩新美欧）宏观政策/产业链/正反面消息面专家，意见与技术面/个股消息面/风险一起交给 DecisionAgent 综合裁决（权重指导调整为技术35%/个股消息面20%/宏观20%/风险25%）；依赖新增的 `search_macro_news` 工具与已扩展的 `search_google_news` 采集链路（collector-service 支持按请求指定语言/地区）。
```

- [ ] **Step 10: Commit**

```bash
git add src/agent/orchestrator.py src/config.py src/agent/agents/decision_agent.py .env.example docs/CHANGELOG.md tests/test_multi_agent.py
git commit -m "feat: wire MacroIntelAgent into full-mode pipeline and DecisionAgent weighting"
```

---

### Task 5: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Syntax-check all changed/new Python files**

Run: `python3 -m py_compile src/services/google_news_collector_client.py src/agent/tools/search_tools.py src/agent/agents/macro_intel_agent.py src/agent/orchestrator.py src/config.py src/agent/agents/decision_agent.py tests/test_google_news_collector_client.py tests/test_search_macro_news_tool.py tests/test_multi_agent.py`
Expected: no output, exit code 0.

- [ ] **Step 2: Run the full offline test suite**

Run: `python3 -m pytest -m "not network"`
Expected: all tests pass except the pre-existing, unrelated 20-failure cross-test-pollution issue documented in `.superpowers/sdd/task-4-report.md` from the earlier collector-service-integration work (`test_alphasift_api.py`/`test_intelligence_service.py`/`test_system_config_api.py`/`test_system_config_service.py`, confirmed unrelated to any of this plan's changes) — if that same set of 20 (and only that set) fails, treat it as already-known and pre-existing, do not attempt to fix it as part of this task. Any OTHER failure must be investigated and fixed before proceeding.

- [ ] **Step 3: Run flake8 critical checks**

Run: `flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics`
Expected: `0`.

- [ ] **Step 4: Confirm diff scope**

Run: `git log --oneline 4ceeae1f..HEAD` and `git diff --stat 4ceeae1f..HEAD`
Expected: only the files touched across Tasks 1-4 appear (`src/services/google_news_collector_client.py`, `src/agent/tools/search_tools.py`, `src/agent/agents/macro_intel_agent.py` (new), `src/agent/orchestrator.py`, `src/config.py`, `src/agent/agents/decision_agent.py`, `.env.example`, `docs/CHANGELOG.md`, `tests/test_google_news_collector_client.py`, `tests/test_search_macro_news_tool.py` (new), `tests/test_multi_agent.py`). No changes to `IntelAgent`, `search_comprehensive_intel`, `search_google_news`'s existing handler, `disagreement.py`, `risk_override.py`.

- [ ] **Step 5: Report final status**

Summarize in your final report: all tests passing (exact counts), flake8 result, confirmation the pre-existing 20-failure cross-test-pollution is the ONLY offline-suite gap (or note any new gap found), and the diff scope confirmation from Step 4.
