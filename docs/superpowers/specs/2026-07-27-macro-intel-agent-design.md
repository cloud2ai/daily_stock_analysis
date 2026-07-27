# MacroIntelAgent：多地区宏观/政策深度洞察设计

- 状态：已批准，待写实现计划
- 日期：2026-07-27
- 范围：新增一个多 Agent 流水线里的宏观/政策专家 Agent，仅在 `full` 模式跑；不改动 `IntelAgent`、`search_comprehensive_intel`、`search_google_news` 的现有行为

## 1. 背景

用户诊断：现有 `search_comprehensive_intel`（`src/search_service.py`）和刚接入的 `search_google_news` 工具都不满足"深度消息面洞察"需求——没有正反面对照检索、没有产业链上下游、没有多地区（日韩新马美欧）覆盖，语言/地区处理还有真实的不一致 bug（详见 2026-07-27 会话记录）。用户核心诉求：现有系统的趋势/结果预判"有时候不是特别准确"，希望引入更多消息面输入辅助技术面判断，且这个能力要**真正参与最终综合判断**，不是一个被晾在一边的附加模块。

前期代码走查确认的关键事实：
- `src/agent/` 下已有一套多 Agent 流水线（`AgentOrchestrator`），`IntelAgent`/`RiskAgent`/`TechnicalAgent`/`DecisionAgent` 依次跑，统一输出 `AgentOpinion`（`signal/confidence/reasoning/raw_data`）。
- 每个 `BaseAgent` 子类天然获得通用的 ReAct 多轮工具调用能力（`run_agent_loop`，`src/agent/runner.py`），由 `tool_names`（可用工具列表）和 `max_steps`（步数预算）配置，不需要为新 Agent 单独建执行引擎。
- `IntelAgent` 已经是"个股新闻/资金流专家"，但只覆盖个股层面，不覆盖国家政策/宏观/多地区/产业链。
- `DecisionAgent` 的 prompt 里已有明确的权重指导文字（技术~40%/消息面~30%/风险~30%），并有一个确定性兜底（`risk_override.py`，高危风险硬性降级）；新 Agent 的 opinion 天然会被这套已有机制消费，不需要新建融合逻辑。
- `search_google_news`（`src/agent/tools/search_tools.py`）目前只接受 `(stock_code, stock_name)`，查询词固定，语言/地区是 `newsgrab/collector-service` 部署层全局配置——不满足"同一次分析、6 个地区各查一次、查询词自由组合"的需求。已通过独立子项目 `newsgrab` 的 `docs/superpowers/specs/2026-07-27-per-request-language-region.md` 解决底层能力（`POST /jobs` 支持按请求覆盖 `language`/`region`，向后兼容）。

## 2. 架构

```
Orchestrator._build_agent_chain（仅 full 模式）：
  TechnicalAgent → IntelAgent（个股新闻，不变） → MacroIntelAgent（新增） → RiskAgent → DecisionAgent（四方权重综合）
```

`MacroIntelAgent` 是一个普通 `BaseAgent` 子类，跟 `IntelAgent` 完全同构：`tool_names=["search_macro_news"]`，`max_steps=6`，system prompt 指导它在 6 个固定地区（CN/JP/KR/SG/US/EU）和多个角度（国家政策/产业链上下游/看多/看空）之间自主决定优先搜什么（小预算，不强求全覆盖），输出统一的 `AgentOpinion`。

## 3. 组件

### 3.1 `src/agent/tools/search_tools.py` 新增 `search_macro_news` 工具

```python
_REGION_LOCALE = {
    "CN": ("zh-CN", "CN"),
    "JP": ("ja", "JP"),
    "KR": ("ko", "KR"),
    "SG": ("en", "SG"),
    "US": ("en", "US"),
    "EU": ("en", "GB"),
}


def _handle_search_macro_news(query: str, region: str) -> dict:
    """Search Google News for a free-text macro/policy/industry query in one
    of 6 fixed regions. Unlike search_google_news, this does NOT persist to
    NewsIntel (not scoped to a single stock_code) and takes a free-text query
    instead of stock_code/stock_name."""
    from src.services import google_news_collector_client

    if region not in _REGION_LOCALE:
        return {"error": f"unknown region {region!r}, must be one of {sorted(_REGION_LOCALE)}"}

    if not google_news_collector_client.is_enabled():
        return {
            "error": "Google News collector is disabled "
                     "(set GOOGLE_NEWS_COLLECTOR_URL to enable)"
        }

    language, country = _REGION_LOCALE[region]
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
                "(may take up to ~40s per call) -- budget your calls. Does NOT "
                "persist results (not scoped to a single stock).",
    parameters=[
        ToolParameter(name="query", type="string", description="Free-text search query, e.g. '钢铁行业 产能政策'"),
        ToolParameter(name="region", type="string", description="One of: CN, JP, KR, SG, US, EU",
                      enum=["CN", "JP", "KR", "SG", "US", "EU"]),
    ],
    handler=_handle_search_macro_news,
    category="search",
    policy=_NEWS_READ_POLICY,
)
```

追加进 `ALL_SEARCH_TOOLS`（第 4 个条目，`search_google_news_tool` 之后）。

**不落库**：`search_google_news`/`search_stock_news` 都以 `(stock_code, stock_name)` 为核心维度写入 `NewsIntel`；`search_macro_news` 的查询词是自由文本、地区是宏观维度，不是"某只股票的新闻"，套用 `_persist_news_response` 的语义不合适，所以这次明确不落库——结果只作为这次分析的 `AgentOpinion.raw_data` 存在，不进 `NewsIntel` 表。

### 3.2 `src/services/google_news_collector_client.py` 扩展

`collect_google_news(query, max_results=5, days=7, timeout_sec=None, language=None, region=None)` —— 新增两个可选参数，透传进 `POST /jobs` 的 `params`（`{"language": language, "region": region}`，为 `None` 时不加进 dict，保持跟 collector-service 那边"不传=用部署默认"的向后兼容约定一致）。

### 3.3 `src/agent/agents/macro_intel_agent.py`（新文件）

```python
class MacroIntelAgent(BaseAgent):
    agent_name = "macro_intel"
    max_steps = 6
    tool_names = ["search_macro_news"]

    def system_prompt(self, ctx: AgentContext) -> str:
        return """\
You are a **Macro & Policy Intelligence Agent**. Your job is to search for
broader market-moving context beyond company-specific news: national policy,
industry-chain (upstream/downstream supply chain) dynamics, and both bullish
and bearish angles -- across multiple regions, since a stock's industry can
be moved by policy or supply-chain news from other markets.

## Available regions
CN, JP, KR, SG, US, EU -- each search_macro_news call targets exactly one.

## Budget
You have at most 6 tool calls. Do NOT try to cover all 6 regions x all
angles -- prioritize the region/angle combinations most relevant to this
stock's industry and listing market. E.g. a steel company likely benefits
most from CN/JP/KR industry-chain and policy searches; a US tech stock from
US/EU policy and bearish-angle searches. Skipping irrelevant regions
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
```

### 3.4 `src/agent/orchestrator.py`

`_build_agent_chain`：`full` 模式的链条从 `[technical, intel, risk, decision]` 改成 `[technical, intel, macro_intel, risk, decision]`（`quick`/`standard`/`specialist` 模式不变）。

`_get_sub_agent_timeout_map`：新增 `("macro_intel", "agent_macro_intel_agent_timeout_s")` 条目，对应 `src/config.py` 新增 `agent_macro_intel_agent_timeout_s: float = 0`（默认 0=不设专属超时，跟其它 Agent 的默认约定一致）。因为 `search_macro_news` 单次调用可能到 40 秒、预算 6 步，部署方如果想设一个专属上限，应该给一个比其它 Agent 更宽松的值（`.env.example` 里要写清楚这一点）。

### 3.5 `src/agent/agents/decision_agent.py`

```
## Signal Weighting Guidelines
- Technical opinion weight: ~35%
- Intel / sentiment weight (company news, capital flow): ~20%
- Macro / policy weight (national policy, industry chain, multi-region): ~20%
- Risk flags weight: ~25% (negative override: any high-severity risk caps signal at "hold")
- If a skill opinion is present, blend it at 20% weight (reducing others proportionally)
```

## 4. 数据流

```
MacroIntelAgent 被 full 模式的 Orchestrator 调度
  → LLM 自主决定调用 search_macro_news(query, region) 0-6 次（有自己的判断，不强制全覆盖）
  → 每次调用：collect_google_news(query, language=.., region=.., max_results=5, days=7)
     → POST {collector_service}/jobs {"backend":"google_news","query":query,"params":{"max_results":5,"days":7,"language":..,"region":..}}
     → 轮询到 done/failed（最多 40s）
  → LLM 综合所有工具调用结果，输出一个 JSON opinion
  → post_process 解析成 AgentOpinion(agent_name="macro_intel", signal, confidence, reasoning, raw_data)
  → 进入 ctx.opinions，被 disagreement.py（多空分歧汇总，现有机制）和 DecisionAgent（四方权重综合，现有机制）自动消费
```

## 5. 错误处理

- 单次 `search_macro_news` 调用失败（collector-service 不可达/超时/region 非法）→ 工具返回 `{"error": ...}` 或 `{"success": False, "error": ...}`，不抛异常，ReAct 循环继续，LLM 自己决定要不要换个地区/角度重试。
- `MacroIntelAgent` 整体失败（JSON 解析失败、超出 `max_steps` 未给出最终答案）→ `post_process` 返回 `None`，跟 `IntelAgent`/`RiskAgent` 现有失败处理方式一致——这个 opinion 缺失，`DecisionAgent` 照常用剩下的 opinions 综合裁决（现有机制，不需要新代码）。
- 不影响其它 Agent：`MacroIntelAgent` 的失败不阻断 `RiskAgent`/`DecisionAgent` 继续跑。

## 6. 测试

- `tests/test_search_google_news_tool.py` 旁边新增 `tests/test_search_macro_news_tool.py`：覆盖 region 非法、未启用、成功、失败四种场景（mock `collect_google_news`），并验证成功路径不调用任何持久化函数（因为设计上就不落库）。
- `tests/test_google_news_collector_client.py` 补充：`collect_google_news(..., language="ja", region="JP")` 时 job 请求体里带了这两个字段；不传时请求体里没有这两个字段（向后兼容回归）。
- 新增 `tests/test_macro_intel_agent.py`（参照现有 Intel/Risk agent 测试的 mock 方式）：验证 system_prompt/tool_names/max_steps 配置正确，`post_process` 正确解析 JSON 为 `AgentOpinion`，JSON 解析失败时返回 `None` 而不抛异常。
- `tests/test_multi_agent.py::test_full_mode`（第 961 行附近，已有 `test_quick_mode`/`test_standard_mode`/`test_full_mode`/`test_invalid_mode_falls_back_to_standard` 一组测试 `_build_agent_chain` 按模式返回的 agent 列表）：更新 `test_full_mode` 的断言，确认 `full` 模式的 chain 包含 `macro_intel`（在 `intel` 之后、`risk` 之前）；`test_quick_mode`/`test_standard_mode` 不需要改（这两个模式不受影响）。
- 不做真实端到端 LLM/collector-service 测试（跟其它 Agent 测试一致，全部 mock）；真实端到端验证放在实现完成后的手动测试阶段，针对一只具体股票跑一次真实 `full` 模式分析。

## 7. 明确不做的事

- 不改 `IntelAgent`、`search_comprehensive_intel`、`search_google_news` 的现有行为——三者都保持不变。
- 不做经济化结构性指标（CPI/PMI/利率等）接入——用户已确认这轮不做，单独后续讨论。
- 不做"正反双搜索独立跑再合并"的确定性机制——延续 trendforge 代码走查的结论，用一次 LLM 综合调用产出带置信度的判断，不是两条独立检索路径合并。
- 不改 `disagreement.py`/`risk_override.py`/`SkillAggregator` 的现有逻辑——`MacroIntelAgent` 的 opinion 天然流入这些已有的通用机制。
- 不新增 `.env.example` 的 `GOOGLE_NEWS_LANGUAGE`/`GOOGLE_NEWS_REGION` 配置项（那是 collector-service 部署层的，这次用的是按请求覆盖，不经过 daily_stock_analysis 自己的环境变量）；只新增 `AGENT_MACRO_INTEL_AGENT_TIMEOUT_S`（跟其它 Agent 超时配置同一约定）。
