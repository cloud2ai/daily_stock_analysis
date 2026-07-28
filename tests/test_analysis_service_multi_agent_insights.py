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
