# -*- coding: utf-8 -*-
"""
MacroIntelAgent — multi-region macro/policy/industry-chain intelligence specialist.

Responsible for:
- Searching national policy and industry-chain (upstream/downstream) news
  across 6 fixed regions: CN, JP, KR, SG, US, EU
- Considering both bullish and bearish angles, not just company-level news
- Producing a structured opinion that feeds into the same weighting/
  disagreement machinery as every other agent -- no new fusion logic needed
"""

from __future__ import annotations

import logging
from typing import Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.runner import try_parse_json

logger = logging.getLogger(__name__)


class MacroIntelAgent(BaseAgent):
    agent_name = "macro_intel"
    max_steps = 6
    tool_names = ["search_macro_news"]

    def system_prompt(self, ctx: AgentContext) -> str:
        return """\
You are a **Macro & Policy Intelligence Agent**. Your job is to search for \
broader market-moving context beyond company-specific news: national policy, \
industry-chain (upstream/downstream supply chain) dynamics, and both bullish \
and bearish angles -- across multiple regions, since a stock's industry can \
be moved by policy or supply-chain news from other markets.

## Available regions
CN, JP, KR, SG, US, EU -- each search_macro_news call targets exactly one.

## Budget
You have at most 6 tool calls. Do NOT try to cover all 6 regions x all \
angles -- prioritize the region/angle combinations most relevant to this \
stock's industry and listing market. E.g. a steel company likely benefits \
most from CN/JP/KR industry-chain and policy searches; a US tech stock from \
US/EU policy and bearish-angle searches. Skipping irrelevant regions \
entirely is expected and correct.

## Angles to consider (compose your own free-text query per call)
- National policy affecting this stock's industry
- Upstream/downstream supply chain dynamics
- Bullish catalysts (positive angle)
- Bearish risks (negative angle)

## Output Format
Return **only** a JSON object:
{
  "signal": "strong_buy|buy|hold|sell|strong_sell",
  "confidence": 0.0-1.0,
  "reasoning": "2-3 sentence summary synthesizing what was found across regions/angles",
  "regions_covered": ["CN", "JP", ...],
  "policy_notes": ["..."],
  "industry_chain_notes": ["..."],
  "bullish_points": ["..."],
  "bearish_points": ["..."]
}
"""

    def build_user_message(self, ctx: AgentContext) -> str:
        parts = [f"Assess macro/policy/industry-chain context for **{ctx.stock_code}**"]
        if ctx.stock_name:
            parts[0] += f" ({ctx.stock_name})"
        parts.append(
            "Decide which 1-6 region/angle combinations are most relevant to this "
            "stock's industry, call search_macro_news for each, then synthesize "
            "one JSON opinion covering what you actually found."
        )
        return "\n".join(parts)

    def post_process(self, ctx: AgentContext, raw_text: str) -> Optional[AgentOpinion]:
        parsed = try_parse_json(raw_text)
        if parsed is None:
            logger.warning("[MacroIntelAgent] failed to parse opinion JSON")
            return None

        ctx.set_data("macro_intel_opinion", parsed)

        return AgentOpinion(
            agent_name=self.agent_name,
            signal=parsed.get("signal", "hold"),
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=parsed.get("reasoning", ""),
            raw_data=parsed,
        )
