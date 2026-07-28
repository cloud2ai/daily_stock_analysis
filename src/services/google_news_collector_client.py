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
    language: Optional[str] = None,
    region: Optional[str] = None,
) -> SearchResponse:
    """Submit a google_news job to collector-service and poll until done/failed
    or the poll budget is exhausted. Never raises. language/region, when given,
    override the collector-service deployment's default for this call only (see
    newsgrab's per-request language/region support).
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
        job_id = _submit_job(base_url, query, max_results, days, language=language, region=region)
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
