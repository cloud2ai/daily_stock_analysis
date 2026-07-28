# 从查询到评分：Agent 编排全流程

本文档梳理"用户查询一只股票"到"最终评分/报告出炉"的完整代码路径，覆盖 `AGENT_ARCH=single/multi` 与
`AGENT_ORCHESTRATOR_MODE` 四种模式（`quick`/`standard`/`full`/`specialist`），并重点说明策略（skill）选择
与宏观情报（MacroIntelAgent）分别在哪个环节介入、跟最终评分是什么关系。

如果本文档与实际代码不一致，以代码为准，并欢迎在相关 PR 里顺手修正本文档。

## 1. 入口收敛

不管是 CLI（`main.py --stocks`）还是 API（`POST /api/v1/analysis/analyze`），最终都收敛到同一个函数：

```
main.py: main() → run_full_analysis() ─┐
                                        ├─▶ StockAnalysisPipeline.process_single_stock()
api: trigger_analysis() → AnalysisService.analyze_stock() ─┘
                                        │
                                        ▼
                    StockAnalysisPipeline.analyze_stock()   (src/core/pipeline.py:379)
```

两条触发路径在进入 `analyze_stock()` 之后完全一致，下文不再区分来源。

## 2. 数据采集（与 Agent 模式无关，先跑）

`analyze_stock()` 内部、在任何 Agent/LLM 逻辑之前，先统一拿到：

- 实时行情（`fetcher_manager.get_realtime_quote()`）
- 筹码分布（`fetcher_manager.get_chip_distribution()`）
- 基本面 / 板块归属（`fetcher_manager.get_fundamental_context()`）
- 技术面趋势分析（`StockTrendAnalyzer.analyze()`）

这一层是共享的，不管后面 `use_agent` 走不走、走哪种模式，这些数据都只拿一次。

## 3. Agent 架构分岔（纯配置，非动态判断）

`src/agent/factory.py::build_agent_executor()` 只读两个环境变量，**不看本次查询内容**：

- `AGENT_ARCH`：`single`（旧的单 Agent ReAct 循环）/ `multi`（`AgentOrchestrator`）
- `AGENT_ORCHESTRATOR_MODE`：`quick` / `standard` / `full` / `specialist`

## 4. 四种编排模式的 Agent 链路

```
quick:      技术面 → decision
standard:   技术面 → 个股消息面 → decision
full:       技术面 → 个股消息面 → 宏观政策 → 风险面 → decision
specialist: 技术面 → 个股消息面 → 风险面 → (最多4个策略专家，并行) → decision
```

| 模式 | Agent 链路 | 策略专家？ | 宏观情报？ | 大致 LLM 调用数 | 定位 |
| --- | --- | --- | --- | --- | --- |
| `quick` | 技术面→decision | 否 | 否 | ~2 | 最快最省，只看技术面 |
| `standard`（默认） | 技术面→个股消息面→decision | 否 | 否 | ~3 | 加个股新闻/情绪，无宏观无风险面 |
| `full` | 技术面→个股消息面→宏观政策→风险面→decision | 否 | **是** | ~5 | 加多区域宏观/政策检索 + 独立风险面，无策略共识 |
| `specialist` | 技术面→个股消息面→风险面→(≤4 策略专家)→decision | **是** | 否 | ~4 + 并行 | 加可解释的多策略确定性共识（`strategy_synthesis`），无宏观政策 |

**⚠️ 现状：`full` 与 `specialist` 互斥。** 目前没有一种模式能同时跑 `MacroIntelAgent` 和策略专家——想要多区域宏观情报就拿不到策略共识，反之亦然。如果未来要"两个都要"，需要改 `orchestrator.py::_build_agent_chain()` 重新设计链路，是有意义但不小的改动。

## 5. 策略选择详解（仅 `specialist` 模式发生）

策略本体存放在仓库根目录 `strategies/*.yaml`（放量突破、缩量回踩、箱体震荡、龙头战法、情绪周期等 16 个），
每个是一段自然语言指令 + 元数据（`market_regimes`、`default_router`、`default_priority` 等），不是代码逻辑。
内部统一术语为 "skill"（`StrategyAgent = SkillAgent` 等均为别名），对用户展示为"策略"。

```
风险面 Agent 跑完
        │
        ▼
ctx.meta 里有明确指定的 skills 列表？（来自 API 请求体 / CLI 配置）
   │是                        │否
   ▼                          ▼
直接使用该列表         AGENT_SKILL_ROUTING = ?
   │                          │
   │                  ┌───────┴────────┐
   │                manual            auto（默认）
   │                  │                 │
   │           用配置写死的列表   读【技术面 Agent】已算好的
   │                  │           ma_alignment / trend_score / volume_status
   │                  │                 │
   │                  │                 ▼
   │                  │         规则判断市场状态（纯规则，无 LLM 调用）：
   │                  │         上升趋势 / 下降趋势 / 横盘 / 剧烈放量 / 热点板块
   │                  │                 │
   │                  │                 ▼
   │                  │         按各策略 YAML 的 market_regimes 标签匹配
   │                  │                 │
   └──────────────────┴─────────────────┘
                       │
                       ▼
        选出最多 4 个策略 → 各建一个 SkillAgent，并行跑（线程池）
                       │
                       ▼
        SkillAggregator 确定性加权：
        权重 = confidence × 历史回测胜率（若样本数 ≥ 30 且开启 AGENT_SKILL_AUTOWEIGHT，否则权重=1.0）
        weighted_score = Σ(signal_score × weight) / Σweight → 映射回 final_signal
                       │
                       ▼
        ConflictDetector 检测策略间分歧（方向对立/分数离散/高置信度异议，规则判断非 LLM）
                       │
                       ▼
        StrategySynthesizer 生成 strategy_synthesis：
        final_signal / weighted_score / confidence（有冲突时打折）/
        conflict_count / consensus_level / supporting_skills / opposing_skills
                       │
                       ▼
   ⚠️ 关键契约：_finalize_dashboard_payload() 会主动删除 LLM 自己写的
      dashboard.strategy_synthesis，强制换成这里算出来的确定性版本
      （"StrategyEngine 是唯一权威来源"，防止 LLM 编造策略共识，
       详见 docs/multi-strategy-contract.md）
                       │
                       ▼
        包装成 agent_name="skill_consensus" 的一条 AgentOpinion，
        塞进 ctx.opinions，一起交给 decision 阶段
```

`_run_strategy_engine()` 在所有模式下都会跑一次，但 `quick`/`standard`/`full` 里没有策略意见，直接返回
`NO_SKILLS`，静默空转，不产生任何数据——这是历史遗留的"无害但容易让人误解"的实现细节。

`LLMDeliberationMediator`（schema 校验的 LLM 冲突调解）在代码里已经完整实现，但**目前没有任何地方实例化它**——
`StrategySynthesizer` 永远用确定性的 `DeliberationMediator()`，"llm_mediator_v1" 这个模式名目前是未接入的
死代码路径，实际"协同推理"是 100% 规则计算，不涉及 LLM。

## 6. DecisionAgent 综合：权重是"建议"，不是代码硬算

```
ctx.opinions = [技术面, 个股消息面, (宏观政策 或 skill_consensus), 风险面]
                       │
                       ▼
    全部拼成文本喂给 DecisionAgent 的 LLM，prompt 里写权重指导：
    技术35% / 消息20% / 宏观20% / 风险25%（策略介入时+20%，其余按比例扣减）
    ← 这只是文字指导，代码里没有任何地方真的按这个百分比做加权求和
                       │
                       ▼
    LLM 自由生成完整 Decision Dashboard JSON
    （sentiment_score / decision_type / signal_attribution /
      phase_decision / battle_plan / intelligence / ...）
```

代码里唯一真正做确定性加权的，是上面第 5 节策略共识内部的 `SkillAggregator`——它只影响
`strategy_synthesis` 这一个字段，**不直接决定 `sentiment_score`**。

## 7. 最终评分（`sentiment_score`）的真实来源

`sentiment_score` 本质是 LLM 自由输出，叠加几层只在特定情况触发的确定性兜底/纠偏：

1. LLM 没给分/给的不合法 → `_estimate_sentiment_score(decision_type, confidence)` 规则化估算。
2. 触发风险熔断（`AGENT_RISK_OVERRIDE`）→ `_adjust_sentiment_score()` 强制把分数夹进对应信号的区间
   （比如降级到 hold 后夹到 40-59）。
3. Agent 模式整体失败 → 退回用纯技术面趋势分析的 `signal_score` 兜底。

`_finalize_dashboard_payload()`（`src/agent/orchestrator.py`）是这几层兜底真正落地的地方。

## 8. 多方观点序列化与页面展示（`full`/`specialist` 模式新增）

`AgentOrchestrator.run()` 结尾把 `ctx.opinions`（各 Agent 的 `signal`/`confidence`/`reasoning`/`raw_data`）
序列化成 `dashboard["agent_opinions"]`，**同时镜像写入 `dashboard["dashboard"]["agent_opinions"]`**——因为
下游 `src/core/pipeline.py::_agent_result_to_analysis_result()` 在展开嵌套 dashboard 结构时只认内层，不镜像
会导致该字段在真实运行中被静默丢弃（已踩过的坑，详见对应 PR）。

三条真实报告构建路径都从同一份 `AnalysisResult.dashboard` 出发，调用同一组 getter
（`get_agent_opinions()` / `get_bullish_bearish_points()`），保证字段契约一致：

- 同步 `/analyze` 响应（`src/services/analysis_service.py::_build_analysis_response`）
- 异步任务轮询的 completed 分支（`api/v1/endpoints/analysis.py::get_analysis_status`）
- 历史详情页（`api/v1/endpoints/history.py::get_history_detail`）

最终产出：

```json
"multi_agent_insights": {
  "opinions": [{"agent_name", "signal", "confidence", "reasoning", "raw_data"}, ...],
  "bullish_points": [{"text", "source_agent"}, ...],
  "bearish_points": [{"text", "source_agent"}, ...],
  "signal_attribution": {"technical_indicators", "news_sentiment", "fundamentals", "market_conditions", ...}
}
```

前端 `apps/dsa-web/src/components/report/MultiAgentInsights.tsx` 把这份数据渲染在报告详情页
"策略点位"和"资讯"之间：各 Agent 意见列表、权重色条、看多/看空双栏（每条标来源 Agent）。

看多/看空聚合规则（`src/analyzer.py::get_bullish_bearish_points()`）：

| Agent | 看多来源 | 看空来源 |
| --- | --- | --- |
| `intel` | `raw_data.positive_catalysts` | `raw_data.risk_alerts` |
| `macro_intel` | `raw_data.bullish_points` | `raw_data.bearish_points` |
| `risk` | 无 | `raw_data.flags[].description` |
| `technical` | 无 | 无 |

**已知、暂不修的 gap**：`signal_attribution` 仍是四桶结构（技术/消息/基本面/市场环境），没有独立的"宏观"桶，
跟 DecisionAgent 权重指导里的"宏观20%"不完全对齐；前端权重色条只能近似展示，不是精确的宏观贡献占比。

## 9. 相关文档

- 策略/skill 共识的详细语义契约（Evidence Chain / Invalid Opinion / 共识度等）：`docs/multi-strategy-contract.md`
- newsgrab（Google News 采集服务）联合部署：`docs/newsgrab-integration.md`
