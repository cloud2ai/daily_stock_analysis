# 单只股票深度分析设计：强化每个 Agent 的推理深度（而非链路结构）

- 状态：设计稿 v3，待用户 review，**本轮不实现**
- 日期：2026-07-29（v3 更新于 2026-08-03）
- 范围：只针对"单只股票分析"这一个任务场景（`AGENT_ORCHESTRATOR_MODE=full`）；"选股/筛选"和"回测"是另外的场景，本文档只埋钩子记录关联性，不在本轮设计
- 变更记录：
  - v1：聚焦"给 full 链路加公司画像+精选策略两个新阶段"。
  - v2：核心结论转向——**真正要解决的问题不是链路结构，而是每一个已有 Agent 自身的推理深度**，v1 的"公司画像"等新阶段构想降级为可选的、次要的输入信号，不再是本设计的主线。
  - v3：把"技术面/策略决策该怎么动态判断"这个 v2 里只有方向、没有具体方法论的部分，用量化投资里成熟的"因子（factor）动态轮动/加权"方法论补完，新增第 3.2 节，并检索了业界最佳实践作为依据（见第 3.2 节末尾"参考资料"）。

## 1. 背景与动机（沿用 v1，補充最终结论）

这份设计源自一次关于"这个项目到底有没有价值"的反思讨论，核心结论：

1. **基于 LLM 的投研分析本质是信息辅助解读，不是校准过的预测模型**。不追求让 `sentiment_score` 更准，而是让分析过程本身更贴近个股实际情况、更可追溯，让"辅助"这件事真正有价值。
2. **信息源没有溯源**：搜索结果本身带 `url`/`source`/`published_date`，但提炼成看多/看空条目时丢失了。
3. **策略选择、技术面判断都没有按个股/情况自身特点做深度判断**：现有各个 Agent 的输出都是"固定检查清单跑一遍、罗列结果"，没有真正的深加工。

在讨论过程中，用户提供了一个自己在另一个内容生产场景里用过的多级研究管线作为参照（选题→角度拆解→跨源检索→筛选→**洞察分析**→事实综合→叙事规划→生成），其中"洞察分析"这一级——把查到的原始信息转化为可信度评估、核心矛盾、隐藏变量、未来信号、独立观点——是最值得借鉴的部分。

**最终收敛的核心判断**（这是本版设计与 v1 最大的不同）：

> 无论链路结构怎么调整，如果每一个节点（Agent）自身的输出不够深，"错误的输入等于错误的输出"，链路层面的优化就没有意义。所以真正的设计重点是：**强化每一个现有 Agent 内部的推理深度**，而不是在链路上加减节点。

同时讨论并否决了两个方向：

- **不重构链路结构**：不需要新增"公司画像"、"精选策略"这类独立的编排阶段（v1 的构想）。
- **不用 Deep Agent 框架替换现有 ReAct 执行引擎**：现有 `run_agent_loop` 这套通用 ReAct 循环，本身不是深度不够的原因——深度不够是因为现有 system prompt 只要求"查完罗列结果"，没有要求"查完之后做深加工"。这是 prompt/schema 层面的问题，不是执行架构的问题。Deep Agent（显式规划、子 Agent 并行委派、持久化草稿区）解决的是"单任务过大装不下一次上下文"、"需要跨会话长周期"这类我们目前没有的问题，换过去只会增加成本、延迟和不可预测性，不会直接换来我们要的"深度"。

## 2. 现状缺陷分析（逐个 Agent，基于实际代码走查）

四个现有 Agent（`src/agent/agents/*.py`）的 system prompt 和输出 schema 走查结论：

### `TechnicalAgent`（`technical_agent.py`）
- 固定 4 步工作流（取数据→跑趋势→跑量能/筹码→识别形态），**不管股票处于什么状态都跑同一套**，不会先判断"这只股票现在该重点看哪个维度"。
- 输出 schema（`trend_score`/`ma_alignment`/`volume_status`/`pattern`）是几个独立的扁平字段，**没有要求做多周期共振检查（日线信号跟周线/月线趋势是否一致）、没有要求做量价背离检测、没有要求评估"这个技术信号是多个独立指标互相印证的稳健信号，还是单一指标临界穿越的脆弱信号"**。
- 结论：技术面判断目前是"报告各指标读数"，不是"综合研判技术面可信度"。

### `IntelAgent`（`intel_agent.py`）
- 固定 5 步工作流（查新闻→综合情报→资金流→分类催化剂/风险→给情绪标签），**同样不区分股票情况**。
- 输出的 `positive_catalysts`/`risk_alerts` 是纯字符串数组，**没有可信度评估（这条消息是否有多个独立信源交叉印证）、没有核心矛盾提炼（比如"看多逻辑"和"看空逻辑"如果同时存在，缺一个"到底哪个更主导"的判断）、没有隐藏变量、没有独立观点**——本质是罗列，不是研判。
- `key_news` 字段有 `title`/`impact`，但没有 `url`，查证据链路已经在实现层面存在（搜索工具本身带 url），只是没有暴露到 Agent 的最终输出里。

### `MacroIntelAgent`（`macro_intel_agent.py`，本次会话新增的 Agent）
- 是四个 Agent 里"查询策略"最深的一个——它的 prompt 明确要求按行业/竞对/贸易关系去组织跨市场查询，而不是死板地查公司名——这部分深度是有的。
- 但**最终综合这一步跟其他 Agent 一样浅**：`policy_notes`/`industry_chain_notes`/`bullish_points`/`bearish_points` 仍然是纯字符串数组，没有可信度评估、没有核心矛盾、没有隐藏变量、没有独立观点。查得深，但沉淀得浅。

### `RiskAgent`（`risk_agent.py`）
- 固定 7 类强制风险检查（内部人减持/业绩预警/监管/行业政策/解禁/估值异常/技术警示），**逐项打勾式检查**，`flags` 数组每条只有 `category`/`severity`/`description`/`source`（这里的 `source` 是自由文本描述"信息从哪来"，不是真实 URL）。
- 没有"这些风险点之间是否有共同根源"的综合判断（比如"减持+估值异常+行业政策收紧"如果同时出现，这背后是不是同一个根本原因），没有"这个风险是孤立事件还是系统性问题"的判断，没有"接下来该盯什么信号确认或排除这个风险"。

**共同缺陷总结**：四个 Agent 都遵循"固定检查清单 → 罗列结果 → 2-3 句话总结"的浅层模式，缺的是同一类东西——查完之后，**有没有真正想一层："这些信息放在一起意味着什么、可信度多高、矛盾在哪、还有什么没显现的因素、接下来该看什么、我自己综合出的判断是什么"**。这正是 v1 设计里提到的"洞察分析"，但它不该是一个新的独立阶段，而应该是每个 Agent 自己输出前必须走的最后一步深加工。

## 3. 设计：共用的"深度洞察"字段契约

给每个 Agent 的输出 schema 统一新增一组字段（字段名跨 Agent 一致，具体内容各自领域填自己的）：

```json
{
  "credibility_assessment": {
    "level": "verified|likely|uncertain|speculative",
    "reasoning": "为什么给这个可信度——比如几个独立信源交叉印证、还是单一依据"
  },
  "core_contradiction": "这个领域里当前最关键的张力/矛盾是什么（允许为空）",
  "hidden_variables": ["表面结论之外，真正驱动判断的深层因素，允许为空数组"],
  "future_signals": ["接下来该盯什么指标/事件，才能验证或推翻这次判断"],
  "independent_viewpoint": "不是复述查到的信息，而是综合出的、别处看不到的判断（1-2句话）"
}
```

这组字段是**在现有输出字段之外新增**，不替换现有的 `signal`/`confidence`/`reasoning`/领域专属字段（比如 `trend_score`、`risk_alerts` 这些继续保留）——现有测试、现有 `get_bullish_bearish_points()` 之类的读取逻辑不受影响，只是 `raw_data` 里多几个字段。

### 3.1 每个 Agent 具体怎么落地

**`TechnicalAgent`**（这块用户特别指出需要更深入设计，不只是加字段）：
- 在跑指标之前，先要求 Agent 自己判断"当前处于什么技术情境"（趋势行情/横盘震荡/突破/背离），**根据情境决定这次重点看哪些指标**，而不是不管什么情况都把 MA/MACD/RSI/量能/形态全部跑一遍、同等权重地报告。
- 强制要求做**多周期共振检查**：日线信号方向是否跟周线/月线趋势一致，不一致时这本身就是 `core_contradiction`。
- 强制要求做**量价背离检查**：价格创新高但指标不确认（或反之），这是技术分析里最经典的"隐藏变量"来源，现在完全没有被结构化地要求检查。
- `credibility_assessment` 对技术面而言，就是"这次信号是几个独立指标共振的结果，还是单一指标临界穿越"。

**`IntelAgent`/`MacroIntelAgent`**：
- `credibility_assessment` 落地为"这条消息有没有多个独立信源报道，还是只有单一来源"。
- `core_contradiction` 落地为"看多逻辑和看空逻辑如果同时存在，当前更主导的是哪个，为什么"。
- 顺带解决信息溯源问题（v1 设计里的 4.4 节）：`positive_catalysts`/`risk_alerts`/`bullish_points`/`bearish_points` 从纯字符串数组升级为带 `source_url`/`source_name`/`published_date` 的结构化对象——这个溯源升级正好可以和"深度洞察"字段一起做，因为两者都是同一个 system prompt/schema 升级动作的一部分，不需要分成两次改动。

**`RiskAgent`**：
- `core_contradiction`/`hidden_variables` 落地为"多个风险 flag 之间是否有共同根源"（比如"减持+估值异常"背后可能是同一个"大股东对公司前景不看好"的根本判断，而不是两件孤立的事）。
- `credibility_assessment` 落地为"这个风险是被多个独立信号印证的系统性问题，还是单一孤立事件"。

### 3.2 技术面/策略决策的动态量化因子框架（v3 新增）

用户提出的原始想法：技术面/策略层面的判断，不应该是固定跑一套指标检查清单，而应该像量化投资里"确定因子"那样——**用过去一段时间（比如 120 个交易日）的价格/量能数据，加上当前的走势相关新闻，动态决定这次该用哪些因子**。这一节把这个想法用业界已有的量化因子方法论补完，回应 v2 里"技术指标需要更深入设计"这一节只有方向、没有具体落地方法论的缺口。

#### 3.2.1 业界最佳实践（检索依据）

- **因子轮动是量化投资里成熟的研究方向，不是我们自己发明的概念**：不同因子在不同经济/市场周期下的历史表现差异很大，通过识别当前所处的 regime（常见方法：趋势过滤、宏观周期指标、统计上的 regime-switching 模型如 sparse jump model），动态调整因子暴露，经回测验证比静态因子配置在风险调整后收益上表现更好（[Dynamic Factor Rotation Strategy: A Business Cycle Approach](https://www.mdpi.com/2227-7072/10/2/46)，[Dynamic Factor Allocation Leveraging Regime-Switching Signals](https://arxiv.org/abs/2410.14841)）。
- **常见因子分类**：动量（momentum）、价值（value）、质量（quality）、低波动（low volatility）、规模（size）。量化基金通常组合多个因子而不是依赖单一因子——因子之间历史上低相关甚至负相关（比如动量和价值），组合后能平滑收益、降低波动（[Quant's Guide to Factor Investing](https://medium.com/@jpolec_72972/quants-guide-to-factor-investing-theory-practice-and-code-09ce1c06c3e8)）。
- **动量因子的回看窗口，学术界和业界都指向 6-12 个月是"甜蜜点"**：太短（1-3个月）噪音大、容易被短期反转干扰；太长（18-24个月）会混入价值投资的长期均值回归效应。经典学术因子用的是"12个月减去最近1个月"（12-1 动量，Fama-French/Carhart 模型的标准做法），但近年（2008-2019）的实践发现 3-6 个月窗口反而比经典 12 个月表现更好（[momentum lookback window 研究](https://www.sciencedirect.com/science/article/abs/pii/S0304405X21000878)）。**用户提出的 120 个交易日（约 6 个月）正好落在业界认可、且近年被认为更优的窗口范围内**，不需要调整。
- **动态因子加权的一个具体、可执行的方法论——IC（Information Coefficient）驱动**：用滚动窗口（研究里常见 20 个交易日）计算每个因子的值和后续实际收益之间的相关系数（IC），IC 高说明这个因子近期确实有效，据此动态加权；更进一步可以用机器学习模型（比如 XGBoost）预测未来 IC 而不是单纯用历史 IC。真实回测显示这种动态加权策略显著跑赢等权重和静态 IC 加权策略（[Dynamic Weighting Multi Factor Stock Selection Strategy Based on XGBoost](https://ieeexplore.ieee.org/document/8690416/)，[A Sustainable Quantitative Stock Selection Strategy Based on Dynamic Factor Adjustment](https://www.mdpi.com/2071-1050/12/10/3978)）。
- **新闻情绪因子的整合是有专门研究支撑的，不是我们臆造的**：把 FinBERT 之类模型算出的情绪指数并入 Fama-French 五因子模型，能显著提升短期收益解释力；关键发现是情绪的影响是"状态依赖"的——平静期（risk-on）和危机期（risk-off）情绪因子的作用方向和强度都不同（[Dynamic Asset Pricing: Integrating FinBERT-Based Sentiment with Fama-French Five-Factor Model](https://arxiv.org/html/2505.01432v1)）。这意味着新闻情绪不该只是一个平行的独立意见，而应该反过来**调制**其他因子（尤其是动量类）该给多大权重。

#### 3.2.2 落到我们系统里的因子分类映射

| 因子类别 | 对应我们系统里的数据/字段 | 备注 |
| --- | --- | --- |
| 动量因子 | 价格动量（用户提出的 120 日窗口）、量能动量 | 现有 `TechnicalAgent` 已经在取日线历史，只是没有按"因子"的方式结构化产出 |
| 均值回归因子 | 乖离率（价格偏离均线的程度） | 仓库 `.env.example` 里已经有 `BIAS_THRESHOLD` 配置项，说明"均值回归"这个概念在系统里已经有雏形，可以正式纳入因子框架，而不是零散的一个阈值配置 |
| 趋势强度因子 | MA 排列的持续性、类 ADX 的趋势强度判断 | 现有 `ma_alignment` 字段是这个因子的浅层版本 |
| 量价关系因子 | 量价背离（v2 已提出）、OBV 类累积/派发判断 | |
| 波动率因子 | 历史波动率水平、当前是波动收敛还是放大阶段 | |
| 消息情绪因子 | `IntelAgent`/`MacroIntelAgent` 产出的 `sentiment_label`/`bullish_points`/`bearish_points` | 按 3.2.1 的研究发现，这个因子应该去调制其他因子权重，不只是平行展示 |
| 宏观因子 | `MacroIntelAgent` 的 `policy_notes`/`industry_chain_notes` | |

#### 3.2.3 动态决定"这次用哪些因子"——分两个深度层级，诚实标注依赖关系

- **轻量版（本轮设计范围内，不需要额外基础设施）**：`TechnicalAgent` 在跑指标之前，先基于已有数据做一次定性的 regime 判断（趋势/横盘/高波动/量价背离），据此决定这次重点看哪几类因子、给出方向性权重描述（而不是精确数字），这一步产出直接写进 v2 已定义的 `credibility_assessment`/`core_contradiction` 字段——技术面的 `core_contradiction` 现在多了一个具体来源：**动量类因子和均值回归类因子如果同时指向相反方向，这本身就是最值得暴露的核心矛盾**。
- **严谨版（本轮明确不做，依赖关系需要记录）**：真正的 IC 驱动动态加权，需要"因子值 vs 后续实际收益"的历史统计数据支撑（滚动窗口计算 IC），**这直接依赖"回测"模块的能力**——这正好呼应了本文档第 8 节"未来讨论钩子"里提到的回测模块。这里明确记录这个依赖关系：如果未来"回测"模块具备了计算历史因子表现的能力，这里的轻量版可以升级成真正数据驱动的 IC 加权版本；在那之前，只能做基于 LLM 判断的定性轻量版。
- **新闻情绪的调制作用（新增设计点）**：`IntelAgent`/`MacroIntelAgent` 的情绪判断输出后，`TechnicalAgent`（或 decision 阶段）在综合时，应该把"当前是不是利空/高不确定性时期"作为调整动量类因子权重的一个输入——比如利空消息密集期，弱化"追涨"类动量因子的权重、强化均值回归/防御性因子的权重。这是一个新的跨 Agent 信息流设计点，具体怎么落地（是 `TechnicalAgent` 自己读取 `ctx` 里 `IntelAgent` 已经产出的情绪标签，还是在 decision 阶段综合）留到写实现计划时再定。

#### 3.2.4 参考资料

- [Dynamic Factor Rotation Strategy: A Business Cycle Approach](https://www.mdpi.com/2227-7072/10/2/46)
- [Dynamic Factor Allocation Leveraging Regime-Switching Signals](https://arxiv.org/abs/2410.14841)
- [Quant's Guide to Factor Investing: Theory, Practice, and Code](https://medium.com/@jpolec_72972/quants-guide-to-factor-investing-theory-practice-and-code-09ce1c06c3e8)
- [Understanding momentum and reversal（回看窗口研究）](https://www.sciencedirect.com/science/article/abs/pii/S0304405X21000878)
- [Dynamic Weighting Multi Factor Stock Selection Strategy Based on XGBoost Machine Learning Algorithm](https://ieeexplore.ieee.org/document/8690416/)
- [A Sustainable Quantitative Stock Selection Strategy Based on Dynamic Factor Adjustment](https://www.mdpi.com/2071-1050/12/10/3978)
- [Dynamic Asset Pricing: Integrating FinBERT-Based Sentiment Quantification with the Fama–French Five-Factor Model](https://arxiv.org/html/2505.01432v1)

### 3.3 对 `multi_agent_insights`/前端的影响

`dashboard["agent_opinions"]` 里每条意见的 `raw_data` 多了这组统一字段后，`MultiAgentInsights.tsx` 可以在现有"技术面/个股消息面/宏观政策/风险面"每一行下面，统一展示"核心矛盾"和"独立观点"这两条最有信息量的字段（不需要为每个 Agent 单独设计展示逻辑，因为字段名是统一的）——这是一个自然的、后续可以做的前端增强，本设计不展开细节。

## 4. 单任务场景下的模式收敛（沿用 v1 的判断，措辞更明确）

针对"单只股票深度分析"这一个任务场景，**`quick`/`standard`/`specialist` 直接废弃，不再作为用户可选项**，只保留（强化后的）`full` 一条线。代码层面是否物理删除、还是保留做内部冒烟测试用途，留到写实现计划时再定；但产品/文档/设置页面层面，这三个模式不再对外呈现。

`specialist` 模式里的策略专家（`SkillAgent`/`SkillRouter`/`SkillAggregator`）——如果本次深度洞察升级验证下来，`TechnicalAgent` 自己已经能给出足够有信息量的技术判断，是否还需要保留独立的策略专家机制，作为开放问题留到后面讨论（不在本版设计里下结论）。

## 5. 数据结构变化一览

| 位置 | 变化 |
| --- | --- |
| `TechnicalAgent`/`IntelAgent`/`MacroIntelAgent`/`RiskAgent` 的 system prompt | 新增"深度洞察"要求：情境判断（技术面专属）、多周期共振检查（技术面专属）、量价背离检查（技术面专属）、可信度评估、核心矛盾、隐藏变量、未来信号、独立观点 |
| 四个 Agent 的输出 schema | 新增 `credibility_assessment`/`core_contradiction`/`hidden_variables`/`future_signals`/`independent_viewpoint` 字段 |
| `IntelAgent`/`MacroIntelAgent` 的 `positive_catalysts`/`risk_alerts`/`bullish_points`/`bearish_points` | 从字符串数组升级为对象数组（`text`/`source_url`/`source_name`/`published_date`），随本次改动一起做 |
| `api/v1/schemas/history.py::MultiAgentPointItem` | 新增可选字段 `source_url`/`source_name`/`published_date` |
| `TechnicalAgent` 的输出 schema（v3 新增） | 新增因子分类相关字段：`regime`（趋势/横盘/高波动/量价背离）、按 3.2.2 因子分类给出的定性权重描述 |
| `AGENT_ORCHESTRATOR_MODE` 的用户可选项 | 从 4 个收敛为对外只呈现 `full` |

全部是新增可选字段，不删除、不重命名现有字段；`_build_agent_chain()` 本身不改。

## 6. 兼容性

- 历史分析记录不会有这组新字段——现有 `get_agent_opinions()`/`get_bullish_bearish_points()` 已经是空值安全的，旧记录展示时新字段自然缺省，不需要迁移脚本。
- `positive_catalysts`/`risk_alerts`/`bullish_points`/`bearish_points` 从字符串数组变成对象数组，是**破坏性的 schema 变化**（不是新增字段），需要在实现计划里明确：`get_bullish_bearish_points()` 等读取方要同时兼容"旧记录是字符串数组"和"新记录是对象数组"两种形态，避免历史数据展示报错。

## 7. 开放问题（需要你确认）

1. **"深度洞察"字段是否要求 Agent 每次都必须填，还是允许合理留空**——比如技术面信息本身很单薄时，"隐藏变量"字段是不是可以就是空数组，而不是被迫编一个出来。倾向于允许留空，但需要你确认。
2. **`specialist` 模式的策略专家机制去留**——如果 `TechnicalAgent` 深度升级后已经足够，策略专家（`SkillAgent`）这套是否还需要保留在这条唯一维护的线里，作为独立议题留到后面讨论。
3. **多周期共振检查需要的数据**——日线之外的周线/月线数据，现有 `TechnicalAgent` 的工具列表（`get_daily_history` 等）是否已经支持按不同周期取数，还是需要新增工具，需要写实现计划时核实。
4. **因子分类是否需要跟 `strategies/*.yaml` 现有的 16 个策略打通**（3.2.2 节的因子分类映射，跟现有策略库的分类逻辑有相似之处但不完全是一回事）——需要你确认这两套分类要不要统一成一套，还是保持各自独立（因子是技术面内部的分析工具，策略是更完整的交易框架，两者概念层级不同，也可以刻意不统一）。
5. **新闻情绪"调制"技术因子权重这个跨 Agent 信息流该怎么实现**（3.2.3 节最后一点）——现有链路顺序是 `技术面 → 个股消息面 → ...`，`TechnicalAgent` 跑在 `IntelAgent` **之前**，此时 `ctx` 里还没有情绪判断结果，`TechnicalAgent` 自己没法在产出时读取到；要实现"情绪调制技术因子权重"，要么把链路顺序调整（`IntelAgent` 提前），要么把"调制"这一步挪到 decision 阶段做（decision 本来就跑在最后，`ctx.opinions` 里已经有所有 Agent 的结果），需要写实现计划时确认，且改链路顺序属于本文档"不改链路结构"这个前提下需要额外权衡的例外情况。

## 8. 未来讨论钩子（本轮不设计，只记录关联性）

用户明确指出，这套"深度洞察"逻辑跟另外两个模块可能有关联，但都留到后面单独讨论：

- **"选"（选股/筛选模块）**：可能跟本设计逻辑相似（同样是对个股做判断），但要注意**筛选场景通常是批量（一次评估几十上百只股票），如果每只都跑一遍这里设计的深度 Agent 流程，成本和延迟会跟"单只深度分析"完全不是一个量级**，需要专门讨论"筛选场景要不要复用同一套深度洞察字段、还是需要一个更轻量的版本"。
- **"回测"模块**：用户明确指出这"可能是另外一套逻辑"——回测本质是对策略/信号做历史统计验证，是确定性的、批量历史数据计算，跟"对当前一只股票做深度定性研判"是两类完全不同的问题，本设计不假设两者共用任何机制。**v3 补充**：第 3.2.3 节明确指出，真正严谨的"IC 驱动动态因子加权"（而不是本轮设计的定性轻量版）需要"因子值 vs 后续实际收益"的历史统计数据，这个能力如果要具备，大概率要靠回测模块支撑——这是"技术面因子框架"和"回测模块"之间一个具体、值得记录的依赖关系，留到讨论回测模块时一并考虑。

这两个钩子只是记录，不在本版设计里展开，等这条单只股票深度分析主线验证完效果之后再回头讨论。
