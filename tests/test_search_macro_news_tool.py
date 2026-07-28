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
