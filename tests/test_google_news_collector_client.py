# -*- coding: utf-8 -*-
"""Tests for the collector-service HTTP client."""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

import requests

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


if __name__ == "__main__":
    unittest.main()
