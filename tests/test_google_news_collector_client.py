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
