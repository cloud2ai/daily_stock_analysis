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
