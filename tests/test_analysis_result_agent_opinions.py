# -*- coding: utf-8 -*-
"""Tests for AnalysisResult.get_agent_opinions() / get_bullish_bearish_points()."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.analyzer import AnalysisResult  # noqa: E402


def _make_result(dashboard):
    # AnalysisResult's only required fields (no default) are code/name/
    # sentiment_score/trend_prediction/operation_advice -- see the
    # @dataclass definition at src/analyzer.py:1669-1686. Everything else
    # defaults, so only those plus `dashboard` need to be set here.
    return AnalysisResult(
        code="600019",
        name="宝钢股份",
        sentiment_score=50,
        trend_prediction="test",
        operation_advice="hold",
        dashboard=dashboard,
    )


def test_get_agent_opinions_returns_dashboard_field_verbatim():
    dashboard = {
        "agent_opinions": [
            {"agent_name": "technical", "signal": "buy", "confidence": 0.72, "reasoning": "MA金叉", "raw_data": {}},
        ]
    }
    result = _make_result(dashboard)
    assert result.get_agent_opinions() == dashboard["agent_opinions"]


def test_get_agent_opinions_returns_empty_list_when_dashboard_missing():
    result = _make_result(None)
    assert result.get_agent_opinions() == []


def test_get_agent_opinions_returns_empty_list_when_key_absent():
    result = _make_result({"core_conclusion": {}})
    assert result.get_agent_opinions() == []


def test_get_bullish_bearish_points_aggregates_per_mapping_table():
    dashboard = {
        "agent_opinions": [
            {
                "agent_name": "intel", "signal": "hold", "confidence": 0.5, "reasoning": "",
                "raw_data": {
                    "positive_catalysts": ["行业复苏"],
                    "risk_alerts": ["股东减持"],
                },
            },
            {
                "agent_name": "macro_intel", "signal": "hold", "confidence": 0.6, "reasoning": "",
                "raw_data": {
                    "bullish_points": ["政策支持"],
                    "bearish_points": ["关税压力"],
                },
            },
            {
                "agent_name": "risk", "signal": "hold", "confidence": 0.4, "reasoning": "",
                "raw_data": {
                    "flags": [{"category": "valuation", "severity": "low", "description": "PB偏低"}],
                },
            },
            {
                "agent_name": "technical", "signal": "buy", "confidence": 0.8, "reasoning": "",
                "raw_data": {},
            },
        ]
    }
    result = _make_result(dashboard)
    points = result.get_bullish_bearish_points()

    assert points["bullish"] == [
        {"text": "行业复苏", "source_agent": "intel"},
        {"text": "政策支持", "source_agent": "macro_intel"},
    ]
    assert points["bearish"] == [
        {"text": "股东减持", "source_agent": "intel"},
        {"text": "关税压力", "source_agent": "macro_intel"},
        {"text": "PB偏低", "source_agent": "risk"},
    ]


def test_get_bullish_bearish_points_handles_missing_or_malformed_raw_data():
    dashboard = {
        "agent_opinions": [
            {"agent_name": "intel", "signal": "hold", "confidence": 0.5, "reasoning": ""},  # no raw_data key at all
            {"agent_name": "macro_intel", "signal": "hold", "confidence": 0.5, "reasoning": "", "raw_data": None},  # raw_data is None
            {"agent_name": "risk", "signal": "hold", "confidence": 0.5, "reasoning": "", "raw_data": {"flags": "not-a-list"}},  # malformed flags
        ]
    }
    result = _make_result(dashboard)
    points = result.get_bullish_bearish_points()
    assert points == {"bullish": [], "bearish": []}


def test_get_bullish_bearish_points_returns_empty_when_dashboard_missing():
    result = _make_result(None)
    assert result.get_bullish_bearish_points() == {"bullish": [], "bearish": []}
