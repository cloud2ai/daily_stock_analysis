# newsgrab collector-service Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `search_google_news` agent tool that calls the standalone `newsgrab` project's `collector-service` async job API over HTTP, giving the LLM agent a Google-News-with-full-content search capability without any in-process import of collection code.

**Architecture:** A new thin HTTP client module (`src/services/google_news_collector_client.py`) submits a job to `collector-service`, polls it to completion within a fixed time budget, and returns a `SearchResponse` (the same dataclass the rest of the search stack already uses). A new agent tool in `src/agent/tools/search_tools.py` wraps that client the same way `search_stock_news`/`search_comprehensive_intel` already wrap `SearchService` — same `ToolDefinition` shape, same `_persist_news_response` persistence call, never raises.

**Tech Stack:** Python, `requests` (existing HTTP convention in this codebase, not `httpx`), `unittest`/`unittest.mock` (existing test convention), the existing `ToolDefinition`/`ToolPolicy` agent-tool framework.

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-07-27-newsgrab-collector-service-integration-design.md` — every task's requirements implicitly include these:

- **No in-process import of any newsgrab collection code.** All communication with `collector-service` is HTTP only (`requests`).
- **`is_enabled()` is a single knob**: `bool(os.environ.get("GOOGLE_NEWS_COLLECTOR_URL", "").strip())`. No separate `_ENABLED` boolean flag.
- **Poll budget**: 2 second interval between polls, 40 second total budget by default, overridable via `GOOGLE_NEWS_COLLECT_TIMEOUT_SEC`. Each individual HTTP request (the initial `POST /jobs` and each `GET /jobs/{id}` poll) has its own 5 second request timeout, separate from the total poll budget.
- **`collect_google_news(query, max_results=5, days=7, timeout_sec=None) -> SearchResponse`** — this exact signature, reusing `src.search_service.SearchResponse`/`SearchResult` (do not define new dataclasses). Never raises — every failure mode (disabled, collector-service unreachable, poll timed out, job returned `failed`) returns `SearchResponse(success=False, error_message=...)`.
- **Article content is truncated to 500 characters** before being stored in `SearchResult.snippet` (`article["content"][:500]`), matching the already-reviewed convention from the abandoned `feat/google-news-collector-plugin` branch's own `collector.py`.
- **Tool parameters are `stock_code`/`stock_name`** (not a free-text `query`), matching `search_stock_news_tool`/`search_comprehensive_intel_tool` exactly. Internally: `query = f"{stock_name} {stock_code}".strip()`.
- **All failure paths collapse to the same shape**: tool handler returns `{"success": False, "error": "<reason>"}` (or `{"error": "..."}` for the disabled case specifically, matching the existing `_handle_search_stock_news` wording style); never raises to the caller.
- **Persistence**: on success, call the existing `_persist_news_response(stock_code=..., stock_name=..., dimension="latest_news", response=response)` — do not modify that function.
- **`search_google_news` is NOT added to `SearchService`'s provider rotation** (`self._providers` / `search_comprehensive_intel`). It is a standalone, on-demand agent tool only.
- **HTTP library is `requests`**, matching this codebase's existing convention (not `httpx`).
- Commit messages in English, no `Co-Authored-By` trailer (repo convention).
- New config must be reflected in `.env.example`; user-visible capability changes must be reflected in `docs/CHANGELOG.md`'s `[Unreleased]` section using the flat `- [类型] 描述` format (no `### ` subheadings inside `[Unreleased]`).

---

### Task 1: `google_news_collector_client.py` — HTTP client for collector-service

**Files:**
- Create: `src/services/google_news_collector_client.py`
- Test: `tests/test_google_news_collector_client.py`

**Interfaces:**
- Consumes: `src.search_service.SearchResponse`, `src.search_service.SearchResult` (existing dataclasses, `query/results/provider/success/error_message/search_time` and `title/snippet/url/source/published_date` respectively — do not modify).
- Produces (used by Task 2):
  - `is_enabled() -> bool`
  - `collect_google_news(query: str, max_results: int = 5, days: int = 7, timeout_sec: Optional[float] = None) -> SearchResponse`

- [ ] **Step 1: Write the failing tests for `is_enabled()`**

```python
# tests/test_google_news_collector_client.py
# -*- coding: utf-8 -*-
"""Tests for the collector-service HTTP client."""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.search_service import SearchResponse, SearchResult  # noqa: E402


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


class IsEnabledTest(unittest.TestCase):
    def test_disabled_when_url_not_set(self):
        from src.services import google_news_collector_client as client
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GOOGLE_NEWS_COLLECTOR_URL", None)
            self.assertFalse(client.is_enabled())

    def test_enabled_when_url_set(self):
        from src.services import google_news_collector_client as client
        with patch.dict(os.environ, {"GOOGLE_NEWS_COLLECTOR_URL": "http://localhost:8001"}):
            self.assertTrue(client.is_enabled())

    def test_disabled_when_url_is_blank_string(self):
        from src.services import google_news_collector_client as client
        with patch.dict(os.environ, {"GOOGLE_NEWS_COLLECTOR_URL": "   "}):
            self.assertFalse(client.is_enabled())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_google_news_collector_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.google_news_collector_client'`

- [ ] **Step 3: Write `is_enabled()` and module skeleton**

```python
# src/services/google_news_collector_client.py
# -*- coding: utf-8 -*-
"""HTTP client for the standalone newsgrab project's collector-service.

Submits an async collection job (POST /jobs) and polls it (GET /jobs/{id})
to completion within a fixed time budget. Never raises -- every failure
mode (disabled, unreachable, poll timed out, job failed) is surfaced as
SearchResponse(success=False, error_message=...), so callers can treat this
exactly like any other search provider in src/search_service.py.

No collection code from newsgrab is imported in-process; all communication
is HTTP only, via `requests`.
"""

import logging
import os
import time
from typing import Optional
from urllib.parse import urljoin

import requests

from src.search_service import SearchResponse, SearchResult

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SEC = 2.0
_REQUEST_TIMEOUT_SEC = 5.0
_DEFAULT_POLL_BUDGET_SEC = 40.0


def is_enabled() -> bool:
    """True iff GOOGLE_NEWS_COLLECTOR_URL is configured (non-empty after strip)."""
    return bool(os.environ.get("GOOGLE_NEWS_COLLECTOR_URL", "").strip())


def _base_url() -> str:
    return os.environ.get("GOOGLE_NEWS_COLLECTOR_URL", "").strip().rstrip("/")


def _poll_budget_sec() -> float:
    raw = os.environ.get("GOOGLE_NEWS_COLLECT_TIMEOUT_SEC", "").strip()
    if not raw:
        return _DEFAULT_POLL_BUDGET_SEC
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_POLL_BUDGET_SEC
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_google_news_collector_client.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/services/google_news_collector_client.py tests/test_google_news_collector_client.py
git commit -m "feat: scaffold google_news_collector_client with is_enabled()"
```

- [ ] **Step 6: Write the failing tests for job submission and polling**

Append to `tests/test_google_news_collector_client.py`:

```python
class CollectGoogleNewsTest(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(
            os.environ, {"GOOGLE_NEWS_COLLECTOR_URL": "http://localhost:8001"}
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    def test_disabled_returns_failure_without_any_request(self):
        from src.services import google_news_collector_client as client
        with patch.dict(os.environ, {"GOOGLE_NEWS_COLLECTOR_URL": ""}), \
             patch("src.services.google_news_collector_client.requests.post") as mock_post:
            result = client.collect_google_news("贵州茅台 600519")
        self.assertFalse(result.success)
        self.assertIn("disabled", result.error_message.lower())
        mock_post.assert_not_called()

    def test_submit_failure_returns_unreachable_message(self):
        from src.services import google_news_collector_client as client
        with patch(
            "src.services.google_news_collector_client.requests.post",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            result = client.collect_google_news("贵州茅台 600519")
        self.assertFalse(result.success)
        self.assertIn("unreachable", result.error_message.lower())

    def test_successful_job_returns_articles_with_snippet_truncated(self):
        from src.services import google_news_collector_client as client

        long_content = "x" * 900
        submit_resp = _mock_response({"job_id": "job-1"}, status_code=201)
        poll_resp = _mock_response({
            "job_id": "job-1",
            "status": "done",
            "result": [
                {
                    "title": "标题",
                    "content": long_content,
                    "url": "https://real-site.example/a",
                    "source": "real-site.example",
                    "published_date": "2026-07-20",
                }
            ],
            "error": None,
        })

        with patch(
            "src.services.google_news_collector_client.requests.post",
            return_value=submit_resp,
        ), patch(
            "src.services.google_news_collector_client.requests.get",
            return_value=poll_resp,
        ), patch(
            "src.services.google_news_collector_client.time.sleep",
            return_value=None,
        ):
            result = client.collect_google_news("贵州茅台 600519")

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "GoogleNews")
        self.assertEqual(len(result.results), 1)
        article = result.results[0]
        self.assertEqual(article.title, "标题")
        self.assertEqual(article.snippet, long_content[:500])
        self.assertEqual(len(article.snippet), 500)
        self.assertEqual(article.url, "https://real-site.example/a")
        self.assertEqual(article.source, "real-site.example")
        self.assertEqual(article.published_date, "2026-07-20")

    def test_job_failed_status_returns_failure_with_server_error(self):
        from src.services import google_news_collector_client as client

        submit_resp = _mock_response({"job_id": "job-2"}, status_code=201)
        poll_resp = _mock_response({
            "job_id": "job-2",
            "status": "failed",
            "result": None,
            "error": "all 3 candidate link(s) failed to yield an article",
        })

        with patch(
            "src.services.google_news_collector_client.requests.post",
            return_value=submit_resp,
        ), patch(
            "src.services.google_news_collector_client.requests.get",
            return_value=poll_resp,
        ), patch(
            "src.services.google_news_collector_client.time.sleep",
            return_value=None,
        ):
            result = client.collect_google_news("贵州茅台 600519")

        self.assertFalse(result.success)
        self.assertEqual(result.error_message, "all 3 candidate link(s) failed to yield an article")

    def test_poll_timeout_returns_failure_mentioning_timed_out(self):
        from src.services import google_news_collector_client as client

        submit_resp = _mock_response({"job_id": "job-3"}, status_code=201)
        pending_resp = _mock_response({
            "job_id": "job-3", "status": "running", "result": None, "error": None,
        })

        fake_time = [1000.0]

        def fake_monotonic():
            fake_time[0] += 3.0  # advance past the tiny test budget every call
            return fake_time[0]

        with patch(
            "src.services.google_news_collector_client.requests.post",
            return_value=submit_resp,
        ), patch(
            "src.services.google_news_collector_client.requests.get",
            return_value=pending_resp,
        ), patch(
            "src.services.google_news_collector_client.time.monotonic",
            side_effect=fake_monotonic,
        ), patch(
            "src.services.google_news_collector_client.time.sleep",
            return_value=None,
        ):
            result = client.collect_google_news("贵州茅台 600519", timeout_sec=5.0)

        self.assertFalse(result.success)
        self.assertIn("timed out", result.error_message.lower())
```

Add `import requests` to the top of the test file (needed for `requests.exceptions.ConnectionError` in the test above):

```python
import requests
```

- [ ] **Step 7: Run test to verify it fails**

Run: `python -m pytest tests/test_google_news_collector_client.py -v`
Expected: FAIL with `AttributeError: module 'src.services.google_news_collector_client' has no attribute 'collect_google_news'`

- [ ] **Step 8: Implement `collect_google_news()`**

Append to `src/services/google_news_collector_client.py`:

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


def _poll_job(base_url: str, job_id: str) -> dict:
    response = requests.get(
        urljoin(base_url + "/", f"jobs/{job_id}"),
        timeout=_REQUEST_TIMEOUT_SEC,
    )
    response.raise_for_status()
    return response.json()


def collect_google_news(
    query: str,
    max_results: int = 5,
    days: int = 7,
    timeout_sec: Optional[float] = None,
) -> SearchResponse:
    """Submit a google_news job to collector-service and poll until done/failed
    or the poll budget is exhausted. Never raises.
    """
    start = time.monotonic()

    if not is_enabled():
        return SearchResponse(
            query=query, results=[], provider="GoogleNews", success=False,
            error_message="Google News collector is disabled "
                          "(set GOOGLE_NEWS_COLLECTOR_URL to enable)",
        )

    base_url = _base_url()
    budget = timeout_sec if timeout_sec is not None else _poll_budget_sec()
    deadline = start + budget

    try:
        job_id = _submit_job(base_url, query, max_results, days)
    except Exception as exc:
        logger.warning("[google_news_collector_client] collector-service unreachable: %s", exc)
        return SearchResponse(
            query=query, results=[], provider="GoogleNews", success=False,
            error_message=f"collector-service unreachable: {exc}",
        )

    status_payload: Optional[dict] = None
    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL_SEC)
        try:
            status_payload = _poll_job(base_url, job_id)
        except Exception as exc:
            logger.warning("[google_news_collector_client] poll request failed, retrying: %s", exc)
            continue
        if status_payload.get("status") in ("done", "failed"):
            break
    else:
        status_payload = None

    if status_payload is None or status_payload.get("status") not in ("done", "failed"):
        logger.warning(
            "[google_news_collector_client] poll budget (%ss) exhausted for %r", budget, query
        )
        return SearchResponse(
            query=query, results=[], provider="GoogleNews", success=False,
            error_message=f"poll timed out after {budget}s",
        )

    if status_payload["status"] == "failed":
        return SearchResponse(
            query=query, results=[], provider="GoogleNews", success=False,
            error_message=status_payload.get("error") or "collection failed",
        )

    articles = status_payload.get("result") or []
    results = [
        SearchResult(
            title=article.get("title", ""),
            snippet=(article.get("content") or "")[:500],
            url=article.get("url", ""),
            source=article.get("source", ""),
            published_date=article.get("published_date"),
        )
        for article in articles
    ]
    return SearchResponse(
        query=query, results=results, provider="GoogleNews", success=True,
        search_time=time.monotonic() - start,
    )
```

Note on the `while ... else` construct used above: the `else` clause on a `while` loop runs only if the loop finished by the condition becoming false (deadline reached) rather than via `break` — so it only fires on a genuine timeout, not when `break` fired for a `done`/`failed` status. This deliberately mirrors Python's `for/else` idiom applied to `while`.

- [ ] **Step 9: Run test to verify it passes**

Run: `python -m pytest tests/test_google_news_collector_client.py -v`
Expected: PASS (8 tests total)

- [ ] **Step 10: Commit**

```bash
git add src/services/google_news_collector_client.py tests/test_google_news_collector_client.py
git commit -m "feat: implement collect_google_news job submit+poll against collector-service"
```

---

### Task 2: `search_google_news` agent tool

**Files:**
- Modify: `src/agent/tools/search_tools.py:1-8` (module docstring), `src/agent/tools/search_tools.py:220-226` (append new tool + extend `ALL_SEARCH_TOOLS`)
- Test: `tests/test_search_google_news_tool.py`

**Interfaces:**
- Consumes: `src.services.google_news_collector_client.is_enabled() -> bool` and `collect_google_news(query, max_results=5, days=7) -> SearchResponse` (from Task 1). Also consumes the existing `_persist_news_response(*, stock_code, stock_name, dimension, response) -> None` and `_NEWS_READ_POLICY` already defined in `search_tools.py:16-21,48-81` — do not modify either.
- Produces: `search_google_news_tool` (a `ToolDefinition`), appended to `ALL_SEARCH_TOOLS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_search_google_news_tool.py
# -*- coding: utf-8 -*-
"""Tests for the search_google_news Agent tool."""

import unittest
from types import SimpleNamespace
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
                title="Google News 标题",
                snippet="正文摘要",
                url="https://real-site.example/article",
                source="real-site.example",
                published_date="2026-07-20",
            )
        ] if success else [],
    )


class SearchGoogleNewsToolTest(unittest.TestCase):
    def test_returns_disabled_message_when_not_enabled(self) -> None:
        from src.agent.tools.search_tools import _handle_search_google_news

        with patch(
            "src.services.google_news_collector_client.is_enabled", return_value=False
        ):
            result = _handle_search_google_news("600519", "贵州茅台")

        self.assertIn("error", result)
        self.assertIn("disabled", result["error"].lower())

    def test_persists_and_returns_results_when_enabled(self) -> None:
        from src.agent.tools.search_tools import _handle_search_google_news

        response = _response("贵州茅台 600519")
        db = SimpleNamespace(save_news_intel=MagicMock(return_value=1))

        with patch(
            "src.services.google_news_collector_client.is_enabled", return_value=True
        ), patch(
            "src.services.google_news_collector_client.collect_google_news",
            return_value=response,
        ), patch("src.agent.tools.search_tools._get_db", return_value=db):
            result = _handle_search_google_news("600519", "贵州茅台")

        self.assertTrue(result["success"])
        self.assertEqual(result["results_count"], 1)
        self.assertEqual(result["provider"], "GoogleNews")
        self.assertEqual(result["results"][0]["snippet"], "正文摘要")
        db.save_news_intel.assert_called_once_with(
            code="600519",
            name="贵州茅台",
            dimension="latest_news",
            query=response.query,
            response=response,
            query_context=None,
        )

    def test_returns_failure_without_persisting_when_collection_fails(self) -> None:
        from src.agent.tools.search_tools import _handle_search_google_news

        response = _response("贵州茅台 600519", success=False)
        db = SimpleNamespace(save_news_intel=MagicMock())

        with patch(
            "src.services.google_news_collector_client.is_enabled", return_value=True
        ), patch(
            "src.services.google_news_collector_client.collect_google_news",
            return_value=response,
        ), patch("src.agent.tools.search_tools._get_db", return_value=db):
            result = _handle_search_google_news("600519", "贵州茅台")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "collection failed")
        db.save_news_intel.assert_not_called()

    def test_unexpected_exception_is_caught_and_returned_as_error(self) -> None:
        from src.agent.tools.search_tools import _handle_search_google_news

        with patch(
            "src.services.google_news_collector_client.is_enabled", return_value=True
        ), patch(
            "src.services.google_news_collector_client.collect_google_news",
            side_effect=RuntimeError("boom"),
        ):
            result = _handle_search_google_news("600519", "贵州茅台")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "boom")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_search_google_news_tool.py -v`
Expected: FAIL with `ImportError: cannot import name '_handle_search_google_news'`

- [ ] **Step 3: Implement the handler and `ToolDefinition`**

Replace the module docstring at the top of `src/agent/tools/search_tools.py` (lines 1-8):

```python
# -*- coding: utf-8 -*-
"""
Search tools — wraps SearchService methods as agent-callable tools.

Tools:
- search_stock_news: search latest stock news
- search_comprehensive_intel: multi-dimensional intelligence search
- search_google_news: Google News search with article content extraction,
  via the standalone newsgrab project's collector-service (HTTP only,
  no in-process import of collection code). Disabled unless
  GOOGLE_NEWS_COLLECTOR_URL is configured.
"""
```

Append at the end of `src/agent/tools/search_tools.py`, replacing the existing final block:

```python
ALL_SEARCH_TOOLS = [
    search_stock_news_tool,
    search_comprehensive_intel_tool,
]
```

with:

```python
# ============================================================
# search_google_news
# ============================================================

def _handle_search_google_news(stock_code: str, stock_name: str) -> dict:
    """Search Google News via the newsgrab collector-service (HTTP), with
    article content extraction. Not part of SearchService's provider
    rotation -- called only when the LLM explicitly invokes this tool."""
    from src.services import google_news_collector_client

    if not google_news_collector_client.is_enabled():
        return {
            "error": "Google News collector is disabled "
                     "(set GOOGLE_NEWS_COLLECTOR_URL to enable)"
        }

    query = f"{stock_name} {stock_code}".strip()
    try:
        response = google_news_collector_client.collect_google_news(
            query, max_results=5, days=7
        )
    except Exception as exc:
        logger.warning("Google News collection failed for %s: %s", stock_code, exc)
        return {"query": query, "success": False, "error": str(exc)}

    if not response.success:
        return {
            "query": response.query,
            "success": False,
            "error": response.error_message,
        }

    _persist_news_response(
        stock_code=stock_code,
        stock_name=stock_name,
        dimension="latest_news",
        response=response,
    )

    return {
        "query": response.query,
        "provider": response.provider,
        "success": True,
        "results_count": len(response.results),
        "results": [
            {
                "title": r.title,
                "snippet": r.snippet,
                "url": r.url,
                "source": r.source,
                "published_date": r.published_date,
            }
            for r in response.results
        ],
    }


search_google_news_tool = ToolDefinition(
    name="search_google_news",
    description="Search Google News for a stock with article content extraction "
                "via a self-hosted collector service (newsgrab). Slower than other "
                "search tools (may take up to ~40s) because it resolves Google News "
                "redirect links and parses article content through an async job. "
                "Only available when GOOGLE_NEWS_COLLECTOR_URL is configured -- check "
                "for an 'error' key indicating it is disabled before relying on it. "
                "Use when other search tools return thin results.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519'",
        ),
        ToolParameter(
            name="stock_name",
            type="string",
            description="Stock name in Chinese, e.g., '贵州茅台'",
        ),
    ],
    handler=_handle_search_google_news,
    category="search",
    policy=_NEWS_READ_POLICY,
)


ALL_SEARCH_TOOLS = [
    search_stock_news_tool,
    search_comprehensive_intel_tool,
    search_google_news_tool,
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_search_google_news_tool.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the existing agent registry test to confirm no regressions**

Run: `python -m pytest tests/test_agent_registry.py -v`
Expected: PASS (all existing tests still pass — `ALL_SEARCH_TOOLS` assertions in this file use `assertGreater(len(...), 0)`, not an exact count, so adding a third tool does not break them)

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools/search_tools.py tests/test_search_google_news_tool.py
git commit -m "feat: add search_google_news agent tool backed by collector-service"
```

---

### Task 3: Config and changelog documentation

**Files:**
- Modify: `.env.example` (append after the `NEWSNOW_BASE_URL` line, currently around line 434)
- Modify: `docs/CHANGELOG.md:9` (the `## [Unreleased]` section)

**Interfaces:**
- Consumes: nothing (docs-only task).
- Produces: nothing consumed by other tasks — this is the last content task before final verification.

- [ ] **Step 1: Add the new config block to `.env.example`**

Find this existing block (search for `NEWSNOW_BASE_URL` — it is the last line of the "新闻时效与分析筛选配置" section):

```bash
# NEWSNOW_BASE_URL=https://newsnow.busiyi.world
```

Add immediately after it:

```bash

# ===================================
# Google News Collector (newsgrab collector-service) — optional HTTP integration
# ===================================
# 独立项目 newsgrab（collector-service）的 base URL；留空则该 agent tool 自动禁用，不发起任何请求
# GOOGLE_NEWS_COLLECTOR_URL=http://localhost:8001
# 单次采集的总轮询预算（秒），超过后判定为超时并优雅降级，不阻塞分析主流程
# GOOGLE_NEWS_COLLECT_TIMEOUT_SEC=40
```

- [ ] **Step 2: Add the changelog entry**

In `docs/CHANGELOG.md`, the `## [Unreleased]` section currently starts:

```markdown
## [Unreleased]
- [chore] 暂停 PR Review 的自动触发，仅保留 `workflow_dispatch` 手动入口，避免辅助评审重复运行及评论权限失败产生误导性红灯；正式 CI 检查保持不变。
```

Add a new line immediately after `## [Unreleased]` (as the new first bullet):

```markdown
## [Unreleased]
- [新功能] 新增 `search_google_news` Agent 工具，通过独立 newsgrab 项目的 collector-service HTTP API 采集 Google News 全文，需配置 `GOOGLE_NEWS_COLLECTOR_URL` 才会启用，未配置时该工具自动禁用、不影响现有分析流程。
- [chore] 暂停 PR Review 的自动触发，仅保留 `workflow_dispatch` 手动入口，避免辅助评审重复运行及评论权限失败产生误导性红灯；正式 CI 检查保持不变。
```

- [ ] **Step 3: Verify no other doc references need updating**

Run: `grep -rn "search_stock_news_tool\|ALL_SEARCH_TOOLS" docs/ README.md 2>/dev/null`
Expected: no results, or only incidental unrelated matches — this confirms there is no README/docs table of agent tools that also needs a new row. If this command DOES find a table listing existing search tools by name, add a matching row for `search_google_news` there before continuing (do not skip this check).

- [ ] **Step 4: Commit**

```bash
git add .env.example docs/CHANGELOG.md
git commit -m "docs: document GOOGLE_NEWS_COLLECTOR_URL config and changelog entry"
```

---

### Task 4: Final verification

**Files:** none (verification only)

**Interfaces:** none — this task only runs checks across everything Tasks 1-3 produced.

- [ ] **Step 1: Syntax-check the changed/new Python files**

Run: `python -m py_compile src/services/google_news_collector_client.py src/agent/tools/search_tools.py tests/test_google_news_collector_client.py tests/test_search_google_news_tool.py`
Expected: no output, exit code 0

- [ ] **Step 2: Run the full offline test suite**

Run: `python -m pytest -m "not network"`
Expected: all tests pass, including the new `tests/test_google_news_collector_client.py` (8 tests) and `tests/test_search_google_news_tool.py` (4 tests)

- [ ] **Step 3: Run flake8 critical checks**

Run: `flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics`
Expected: `0` (no critical syntax/undefined-name errors)

- [ ] **Step 4: Run the full backend CI gate**

Run: `./scripts/ci_gate.sh`
Expected: `==> backend-gate: all checks passed`

If this fails on a pre-existing, unrelated check (e.g. a flaky deterministic check unrelated to this change), note it explicitly rather than silently working around it — do not modify unrelated files to force this script green.

- [ ] **Step 5: Confirm the diff is minimal and matches scope**

Run: `git diff --stat aa68d45d..HEAD` (or `main..HEAD` if `main` has not moved) and `git log --oneline aa68d45d..HEAD`

Expected: only the files listed in Tasks 1-3 appear (`src/services/google_news_collector_client.py`, `src/agent/tools/search_tools.py`, `.env.example`, `docs/CHANGELOG.md`, `tests/test_google_news_collector_client.py`, `tests/test_search_google_news_tool.py`, plus the design spec doc committed before this plan). No changes to `plugins/`, no changes to `search_comprehensive_intel`'s provider rotation, no changes inside the `newsgrab` repo (which is a separate directory entirely and out of scope).

- [ ] **Step 6: Report final status**

Summarize in your final report: all tests passing (exact counts), `ci_gate.sh` result, and the two explicitly-known unverified items carried over from the design spec (§7/§8): (a) no real end-to-end run against a live `collector-service` was performed as part of this plan — the client is unit-tested against mocked HTTP responses only; (b) whether the agent runtime's dynamic per-tool `timeout_seconds` budget (set elsewhere, in `src/agent/executor.py`/`orchestrator.py`) is compatible with this tool's 40s internal poll budget has not been separately verified.
