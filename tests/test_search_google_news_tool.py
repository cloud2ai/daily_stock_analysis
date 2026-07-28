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
