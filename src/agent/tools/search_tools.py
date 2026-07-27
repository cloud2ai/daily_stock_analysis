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
- search_macro_news: multi-region (CN/JP/KR/SG/US/EU) free-text Google News
  search for national policy/industry-chain/bullish/bearish angles, via the
  same collector-service. Not scoped to a stock_code, does not persist.
"""

import logging

from src.agent.tools.registry import ToolParameter, ToolDefinition, ToolPolicy

logger = logging.getLogger(__name__)

_NEWS_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read", "db_write_cache"],
    permissions=["news:read"],
    scope_dimensions=["stock"],
)
_INTEL_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read", "db_write_cache"],
    permissions=["intel:read"],
    scope_dimensions=["stock"],
)


def _get_db():
    """Lazy import for DatabaseManager."""
    from src.storage import get_db
    return get_db()


def _get_search_service():
    """Return shared SearchService singleton."""
    from src.search_service import get_search_service
    return get_search_service()


def _canonical_search_code(stock_code: str) -> str:
    from data_provider.base import canonical_stock_code, normalize_stock_code

    return canonical_stock_code(normalize_stock_code(str(stock_code or "").strip()))


def _persist_news_response(
    *,
    stock_code: str,
    stock_name: str,
    dimension: str,
    response,
) -> None:
    """Best-effort news persistence for Agent search tools."""
    if not response or not getattr(response, "success", False) or not getattr(response, "results", None):
        return

    code = _canonical_search_code(stock_code)
    try:
        saved_count = _get_db().save_news_intel(
            code=code,
            name=stock_name,
            dimension=dimension,
            query=response.query,
            response=response,
            query_context=None,
        )
        logger.info(
            "Agent news intel persisted for %s (dimension=%s, new_records=%s)",
            code,
            dimension,
            saved_count,
        )
    except Exception as exc:
        logger.warning(
            "Agent news intel persistence failed for %s (dimension=%s): %s",
            code,
            dimension,
            exc,
        )


def _handle_search_stock_news(stock_code: str, stock_name: str) -> dict:
    """Search latest news for a stock."""
    service = _get_search_service()

    if not service.is_available:
        return {"error": "No search engine available (no API keys configured)"}

    response = service.search_stock_news(stock_code, stock_name, max_results=5)

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


search_stock_news_tool = ToolDefinition(
    name="search_stock_news",
    description="Search for the latest news articles about a specific stock. "
                "Requires both stock_code and stock_name for accurate search. "
                "Returns news titles, snippets, sources, and URLs.",
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
    handler=_handle_search_stock_news,
    category="search",
    policy=_NEWS_READ_POLICY,
)


# ============================================================
# search_comprehensive_intel
# ============================================================

def _handle_search_comprehensive_intel(stock_code: str, stock_name: str) -> dict:
    """Multi-dimensional intelligence search."""
    service = _get_search_service()

    if not service.is_available:
        return {"error": "No search engine available (no API keys configured)"}

    intel_results = service.search_comprehensive_intel(
        stock_code=stock_code,
        stock_name=stock_name,
        max_searches=6,
    )

    if not intel_results:
        return {"error": "Comprehensive intel search returned no results"}

    # Format into readable report
    report = service.format_intel_report(intel_results, stock_name)

    # Also return structured data
    dimensions = {}
    for dim_name, response in intel_results.items():
        if response and response.success:
            _persist_news_response(
                stock_code=stock_code,
                stock_name=stock_name,
                dimension=dim_name,
                response=response,
            )
            dimensions[dim_name] = {
                "query": response.query,
                "results_count": len(response.results),
                "results": [
                    {
                        "title": r.title,
                        "snippet": r.snippet,
                        "source": r.source,
                    }
                    for r in response.results[:3]  # limit to 3 per dimension to save tokens
                ],
            }

    return {
        "report": report,
        "dimensions": dimensions,
    }


search_comprehensive_intel_tool = ToolDefinition(
    name="search_comprehensive_intel",
    description="Multi-dimensional intelligence search: latest news, market analysis, "
                "risk checking, earnings outlook, and industry trends for a stock. "
                "Returns a formatted report and structured results.",
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
    handler=_handle_search_comprehensive_intel,
    category="search",
    policy=_INTEL_READ_POLICY,
)


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
