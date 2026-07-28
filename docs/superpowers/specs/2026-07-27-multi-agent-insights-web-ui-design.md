# 多 Agent 意见可视化（Web 前端）设计

- 状态：已批准（用户委托自主决策剩余细节），待写实现计划
- 日期：2026-07-27
- 范围：仅"实时分析响应"路径（用户当次跑分析立刻在报告主页面看到）；历史报告详情页打通、完整多语言覆盖明确排除在本轮之外

## 1. 背景

`MacroIntelAgent` 已经在后端 `full` 模式的多 Agent 流水线里跑起来了（`[technical, intel, macro_intel, risk, decision]`），但用户指出：接入这个能力之后，网页上理论上应该有对应的展示区，而且应该"综合到看空还是看多的展示里面"。

代码走查确认了两件事：
1. **前端现状**：`apps/dsa-web` 现在完全没有任何"分 Agent 展示各自意见"的先例（哪怕是早就存在的 IntelAgent/RiskAgent 也没有）。`signal_attribution`/`intelligence`/看多看空相关字段目前只以纯文本 markdown 形式存在于一个额外的"查看完整报告"抽屉里，不是报告主页面上的常驻卡片/图表。
2. **后端现状（本轮设计前发现的关键点）**：`AgentOrchestrator.run()`（`src/agent/orchestrator.py:352-375`）内部维护的 `ctx.opinions`（每个 Agent 的完整 `AgentOpinion`：`agent_name/signal/confidence/reasoning/raw_data`）**目前完全没有被序列化进最终返回的 `dashboard`**——它只是流水线执行期间的内存态，被 `DecisionAgent` 自己读取用来生成最终综合 JSON，之后就丢弃了。之前误以为的复用对象 `AgentDisagreementExplanation`（`src/schemas/report_schema.py:312`）经确认属于另一个更专门的"决策信号"子系统（`base_disagreement`/`risk_control`/`pipeline_termination` 这类字段），跟这次的 `full` 模式编排流水线不是一回事，不能复用。

**结论：这次真正要做的不是"把已有的隐藏字段摘出来展示"，而是要新增一步——把 `ctx.opinions` 在流水线跑完时序列化进 `dashboard`，作为一个全新的持久化字段，再走到前端。**

## 2. 范围与展示形态（用户已确认的决策）

- **统一"多方观点"展示区**，不是只给 MacroIntelAgent 单独开一个孤立卡片；同时汇总 Technical/Intel/MacroIntel/Risk 四个 Agent 的贡献。
- **位置**：报告主页面常驻区域，`ReportSummary.tsx` 里新增一个 section（不需要点开抽屉）。
- **权重展示**：`signal_attribution` 的四个百分比做成一条横向堆叠条（技术/个股消息/宏观/风险四段颜色拼接，悬停显示具体数字）。
- **看多看空**：左右两栏卡片。
- **数据链路范围**：仅打通"实时分析响应"（用户当次跑分析、`_build_analysis_response` 返回给前端立即展示）；历史报告详情页（`historyApi.getMarkdown` 那条路径）**明确不在本轮范围内**，留作后续独立子项目。
- **多语言范围**：跟随现有 `apps/dsa-web/src/utils/reportLanguage.ts` 的 zh/en/ko 三语言约定，新增文案补齐这三种语言的翻译（不引入新的 i18n 机制，也不新增本轮未覆盖的语言）。

## 3. 架构

```
AgentOrchestrator.run()（新增一步）
  → ctx.opinions（技术/个股消息/宏观/风险 四个 AgentOpinion，流水线跑完时的最终状态）
  → 序列化成 dashboard["agent_opinions"] = [{agent_name, signal, confidence, reasoning}, ...]
  → 正常流程：DecisionAgent 的综合 JSON 仍然是 dashboard 本体，agent_opinions 是新增的兄弟字段

AnalysisResult（src/analyzer.py）新增：
  get_agent_opinions() -> List[Dict]          # 从 self.dashboard['agent_opinions'] 取
  get_bullish_bearish_points() -> Dict         # 按 agent_name 分类规则，从各 opinion 的 raw_data 里聚合看多/看空条目

src/services/analysis_service.py::_build_analysis_response 新增：
  report["multi_agent_insights"] = {
    "opinions": [...],              # 直接来自 get_agent_opinions()
    "bullish_points": [...],        # [{text, source_agent}, ...]
    "bearish_points": [...],        # [{text, source_agent}, ...]
    "signal_attribution": {...},    # 复用现有 dashboard.signal_attribution（这次首次摘出到前端）
  }

apps/dsa-web 新增：
  types/analysis.ts:  MultiAgentInsights 相关接口
  utils/reportLanguage.ts:  补齐新文案的 zh/en/ko 翻译
  components/report/MultiAgentInsights.tsx:  新组件
  components/report/ReportSummary.tsx:  接入新 section（放在 ReportStrategy 之后、ReportNews 之前）
```

## 4. 数据映射细节

### 4.1 "多方观点"汇总行

来自 `dashboard.agent_opinions`，每个 Agent 一行，展示 agent 中文/英文/韩文标签（技术面/个股消息面/宏观政策/风险面）+ `signal` + `confidence`。

### 4.2 看多/看空聚合规则

不是每个 Agent 都有itemized 的看多看空条目——`TechnicalAgent` 只有 `signal`/`reasoning`/`trend_score` 等结构化字段，没有条目列表，因此**不贡献**看多看空条目（只出现在 4.1 的汇总行里）。实际聚合规则：

| Agent | 看多来源字段 | 看空来源字段 |
|---|---|---|
| `intel` | `raw_data.positive_catalysts`（字符串列表） | `raw_data.risk_alerts`（字符串列表） |
| `macro_intel` | `raw_data.bullish_points`（字符串列表） | `raw_data.bearish_points`（字符串列表） |
| `risk` | 无 | `raw_data.flags[].description`（结构化风险条目取 description 字段） |
| `technical` | 无 | 无 |

每条聚合出来的看多/看空条目都带上来源 Agent 标签（如"消息面"/"宏观"/"风险"），不是匿名罗列——这样用户能看出这条判断是从哪个 Agent 来的，也是这次设计明确要避免的"孤立展示"问题的解法。

### 4.3 signal_attribution 直通

`dashboard.signal_attribution`（`technical_indicators`/`news_sentiment`/`fundamentals`/`market_conditions` 四个百分比）首次被摘取到前端响应里，不需要新的后端计算逻辑，只是这次终于把它接出来。

## 5. 组件设计（`MultiAgentInsights.tsx`）

```
┌─ 多方观点 ────────────────────────────────────┐
│ 技术面    buy    0.72                          │
│ 个股消息  hold   0.55                          │
│ 宏观政策  hold   0.60                          │
│ 风险面    ⚠️ (若 risk opinion 存在)             │
├─ 权重占比（横向堆叠条）────────────────────────┤
│ [技术35%][消息20%][宏观20%][风险25%]           │
├─ 看多 ──────────────┬─ 看空 ────────────────────┤
│ • 条目（来源标签）   │ • 条目（来源标签）        │
│ • ...               │ • ...                    │
└─────────────────────┴──────────────────────────┘
```

- 复用现有 `Card`/`DashboardPanelHeader` 组件（`ReportStrategy.tsx` 已经用到的那套，保持视觉风格一致）。
- 若 `dashboard.agent_opinions` 不存在（旧版本分析记录、或非 `full` 模式跑出来的报告，例如 `quick`/`standard` 模式没有 `macro_intel`/`risk`），组件整体返回 `null`（参照 `ReportStrategy` 现有的 `if (!strategy) return null` 模式），不展示半截或报错。
- `quick`/`standard` 模式下 `agent_opinions` 里自然只有实际跑过的那几个 Agent（比如 `standard` 模式没有 `macro_intel`/`risk`），组件按实际存在的条目渲染，不假设固定 4 个。

## 6. 错误处理

- 后端：`get_agent_opinions()`/`get_bullish_bearish_points()` 在 `dashboard` 缺失、`agent_opinions` 键不存在、或某个 opinion 的 `raw_data` 不是预期格式时，均返回空列表/空字典，不抛异常——延续 `get_sniper_points()` 等现有 getter 的容错风格。
- 前端：`MultiAgentInsights` 组件在 `multiAgentInsights` prop 为空或 `opinions`/`bullishPoints`/`bearishPoints` 全部为空时返回 `null`。

## 7. 测试

- 后端：`tests/test_multi_agent.py` 新增 `AgentOrchestrator.run()` 序列化 `agent_opinions` 的测试（mock 一组 `ctx.opinions`，断言 dashboard 里出现对应字段）；`tests/test_analyzer.py`（或等价文件，需先确认实际文件名）新增 `get_agent_opinions()`/`get_bullish_bearish_points()` 的单元测试，覆盖：正常聚合、`risk.flags` 结构化条目转文本、某 Agent 缺失 raw_data 字段时不报错、`dashboard` 为 `None` 时返回空。
- 前端：新增 `MultiAgentInsights.test.tsx`（需先确认 `apps/dsa-web` 现有测试框架和惯例，参照 `ReportStrategy` 等现有组件的测试写法），覆盖：正常渲染、`multiAgentInsights` 为空时返回 null、`bullishPoints`/`bearishPoints` 其中一栏为空时另一栏仍正常展示。

## 8. 明确不做的事

- 不打通历史报告详情页（`historyApi.getMarkdown` 路径）——这是后续独立子项目。
- 不改 `report_markdown.j2` 模板（现有的纯文本 markdown 展示保持不变，两条路径暂时并存，不重复也不冲突）。
- 不新增除 zh/en/ko 之外的语言。
- 不改 `signal_attribution` 的四桶结构本身（仍然是技术/消息/基本面/市场环境四个桶，不新增"宏观"独立桶——`DecisionAgent` prompt 层面的四方权重指导跟这个 schema 桶数不一致，是一个已知的、这次不修的既有 gap，本设计只是把现有四桶原样摘出展示）。
