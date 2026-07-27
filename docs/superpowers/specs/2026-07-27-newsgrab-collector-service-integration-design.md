# newsgrab collector-service 集成设计（daily_stock_analysis 侧）

- 状态：已批准，待写实现计划
- 日期：2026-07-27
- 范围：仅"HTTP 客户端集成"——新增一个调用外部 newsgrab/collector-service 的 agent tool；不改动 newsgrab 自身，不改动 `search_comprehensive_intel` 的 provider 轮询，不做决策链/报告结构层面的新逻辑

## 1. 背景

`daily_stock_analysis` 此前在 `feat/google-news-collector-plugin` 分支上实现过一套进程内 Google News 采集插件（`plugins/google_news_collector/`），完整测试+审查通过（详见 `docs/superpowers/specs/2026-07-25-google-news-collection-design.md`），但复审过程中发现进程内 import 采集代码无法彻底解决依赖环境共享风险（真实遇到过 `newspaper3k`/`newspaper4k` 包名冲突、全局 `socket.setdefaulttimeout` 状态污染两个具体问题），只能不断打补丁应对。用户决定：该分支不合入，采集能力改为完全独立的项目 **newsgrab**（`/home/ubuntu/workspace/newsgrab`，独立仓库，两个容器：`playwright-service` 浏览器自动化 + `collector-service` 采集编排），`daily_stock_analysis` 以后只通过 HTTP API 调用它，绝不进程内 import 任何采集代码。

newsgrab 现状：两个服务已完整实现、单元测试通过、Docker 构建验证通过、经过独立的架构/实现/审查流程。`collector-service` 暴露的 API 契约（已验证，视为稳定接口）：

- `POST /jobs`，body `{"backend": "google_news", "query": str, "params": {"max_results": int, "days": int}}`，返回 `201 {"job_id": str}`，异步执行，不阻塞。
- `GET /jobs/{job_id}`，返回 `{"job_id": str, "status": "pending"|"running"|"done"|"failed", "result": [{"title","content","url","source","published_date"}, ...] | null, "error": str | null}`；`job_id` 不存在返回 404。
- 采集是异步 job 模型：提交后需轮询 `GET /jobs/{id}` 直到 `done`/`failed`。
- 无认证，安全边界是部署时的网络可达性（内部 Docker network 或等效网络隔离），由部署方保证，不在本设计范围内。
- 单条候选链接失败会被跳过；只有当**全部**候选链接都失败时 job 才整体标记为 `failed`；gnews 本身未查到任何候选链接时，是正常的 `done` + 空列表（不是失败）。

## 2. 现状集成点勘查

沿用 `docs/superpowers/specs/2026-07-25-google-news-collection-design.md` 第 3 节已勘查的结论，关键结论：

- `src/agent/tools/search_tools.py` 的 `ToolDefinition` 注册模式（`search_stock_news_tool`/`search_comprehensive_intel_tool`）和 `_persist_news_response()` 辅助函数可直接复用，新增一个平级 tool 即可，不改动既有 tool。
- `src/search_service.py:SearchResponse`/`SearchResult`（`query/results/provider/success/error_message/search_time` 与 `title/snippet/url/source/published_date/...`）是稳定、可复用的返回值形状，`db.save_news_intel`（`src/storage.py:1845`）只需要一个 `SearchResponse` 对象，不需要感知 provider 注册列表。
- `search_comprehensive_intel` 内的 provider 轮询是单只/批量分析共用的同步阻塞路径，新数据源**不**应加入 `self._providers` 参与自动轮询，否则会拖慢批量分析——这次同样不参与，仅作为 LLM 按需调用的独立 tool。

新增结论（本次特有）：

- 已废弃分支的 `plugins/google_news_collector/collector.py::collect_google_news()` 直接返回 `SearchResponse`（`provider="GoogleNews"`），把解析出的正文截断到 500 字符存入 `SearchResult.snippet`（`article.get("content", "")[:500]`）。这次沿用同样的返回类型和截断长度，使 `_handle_search_google_news` 与 `_persist_news_response` 的调用方式和已废弃分支几乎一致，唯一的区别是内部实现从"进程内插件函数调用"换成"HTTP 调用 collector-service"。
- `src/agent/tool_surface.py` 在 handler 之外还有一层基于 `ThreadPoolExecutor` + `future.result(timeout=...)` 的外层超时保护（`timeout_seconds` 来自 agent 运行时的动态剩余预算，非固定常量，参见 `src/agent/executor.py`/`orchestrator.py`/`research.py`）。本设计内部的轮询总预算（见第 4.4 节）必须明确写在 tool 描述里，提醒使用方：如果某次调用时 agent 运行时分配给该 tool 的 `timeout_seconds` 小于这个轮询预算，会被外层提前 kill，导致本设计的"优雅降级"逻辑没有机会执行——这不是本设计能控制的边界，只能通过文档提醒。

## 3. 架构设计

### 3.1 模块划分

```
src/services/google_news_collector_client.py   # 新增：collector-service HTTP 客户端
src/agent/tools/search_tools.py                # 修改：新增 search_google_news tool
.env.example                                   # 修改：新增 GOOGLE_NEWS_COLLECTOR_URL / GOOGLE_NEWS_COLLECT_TIMEOUT_SEC
docs/CHANGELOG.md                              # 修改：[Unreleased] 追加一行
tests/test_google_news_collector_client.py     # 新增
tests/test_search_google_news_tool.py          # 新增（若已废弃分支上有同名文件，以本次内容为准重写）
```

`src/services/google_news_collector_client.py` 与现有 `src/services/*_service.py`（如 `alphasift_service.py`）同级、命名风格一致，是一个独立的外部服务集成模块，不依赖 `plugins/` 目录（该目录本身随已废弃分支一起不合入）。

### 3.2 `google_news_collector_client.py` 的对外接口

```python
def is_enabled() -> bool:
    """True iff GOOGLE_NEWS_COLLECTOR_URL is set (non-empty). Single on/off knob."""

def collect_google_news(
    query: str,
    max_results: int = 5,
    days: int = 7,
    timeout_sec: Optional[float] = None,
) -> SearchResponse:
    """Submit a google_news job to collector-service and poll until done/failed
    or the timeout budget is exhausted. Never raises -- every failure mode
    (disabled, unreachable, poll timeout, job failed) returns
    SearchResponse(success=False, error_message=...). On success, returns
    SearchResponse(provider="GoogleNews", results=[SearchResult(...), ...]).
    """
```

签名和返回类型与已废弃分支的 `collect_google_news()` 保持一致，这样 `search_tools.py` 里的调用方代码可以直接复用已废弃分支审查通过的写法（仅替换 import 路径）。

内部实现：

1. `is_enabled()` 为 `False` → 直接返回 `SearchResponse(query=query, results=[], provider="GoogleNews", success=False, error_message="Google News collector disabled (set GOOGLE_NEWS_COLLECTOR_URL to enable)")`。
2. 计算 `deadline = time.time() + (timeout_sec or _timeout_budget_sec())`（`_timeout_budget_sec()` 读取 `GOOGLE_NEWS_COLLECT_TIMEOUT_SEC`，默认 `40`）。
3. `POST {base_url}/jobs`（单次请求超时 5s，与轮询总预算分开配置）提交 job；网络异常/非 201 → 捕获，返回 `success=False, error_message="collector-service unreachable: ..."`。
4. 轮询循环：每 2 秒 `GET {base_url}/jobs/{job_id}`（单次请求超时 5s）；单次轮询请求异常时记录日志并继续重试（不立即判死刑），直到 `deadline`；
   - 收到 `status == "done"`：把 `result` 里的每篇 article 映射成 `SearchResult(title=article["title"] or 原始候选标题, snippet=article["content"][:500], url=article["url"], source=article["source"], published_date=article["published_date"])`，返回 `SearchResponse(success=True, results=[...], provider="GoogleNews", search_time=...)`。
   - 收到 `status == "failed"`：返回 `SearchResponse(success=False, error_message=result 里的 error 或默认文案)`。
   - 循环耗尽 `deadline` 仍未见到 `done`/`failed`：返回 `SearchResponse(success=False, error_message="poll timed out after {N}s")`。

### 3.3 `search_tools.py` 新增 tool

```python
def _handle_search_google_news(stock_code: str, stock_name: str) -> dict:
    """Search Google News via the newsgrab collector-service (HTTP), with
    full-article-content extraction. Not part of SearchService's provider
    rotation -- called only when the LLM explicitly invokes this tool."""
    from src.services import google_news_collector_client

    if not google_news_collector_client.is_enabled():
        return {
            "error": "Google News collector is disabled "
                     "(set GOOGLE_NEWS_COLLECTOR_URL to enable)"
        }

    query = f"{stock_name} {stock_code}".strip()
    try:
        response = google_news_collector_client.collect_google_news(
            query, max_results=5, days=7
        )
    except Exception as exc:
        logger.warning("Google News collection failed for %s: %s", stock_code, exc)
        return {"query": query, "success": False, "error": str(exc)}

    if not response.success:
        return {"query": response.query, "success": False, "error": response.error_message}

    _persist_news_response(
        stock_code=stock_code, stock_name=stock_name,
        dimension="latest_news", response=response,
    )

    return {
        "query": response.query,
        "provider": response.provider,
        "success": True,
        "results_count": len(response.results),
        "results": [
            {"title": r.title, "snippet": r.snippet, "url": r.url,
             "source": r.source, "published_date": r.published_date}
            for r in response.results
        ],
    }


search_google_news_tool = ToolDefinition(
    name="search_google_news",
    description="Search Google News for a stock with article content extraction "
                "via a self-hosted collector service (newsgrab). Slower than other "
                "search tools (may take up to ~40s) because it resolves Google News "
                "redirect links and parses article content through an async job. "
                "Only available when GOOGLE_NEWS_COLLECTOR_URL is configured -- check "
                "for an 'error' key indicating it is disabled before relying on it. "
                "Use when other search tools return thin results.",
    parameters=[
        ToolParameter(name="stock_code", type="string", description="Stock code, e.g., '600519'"),
        ToolParameter(name="stock_name", type="string", description="Stock name in Chinese, e.g., '贵州茅台'"),
    ],
    handler=_handle_search_google_news,
    category="search",
    policy=_NEWS_READ_POLICY,
)
```

`search_google_news_tool` 追加进 `ALL_SEARCH_TOOLS` 列表，与 `search_stock_news_tool`/`search_comprehensive_intel_tool` 平级。

### 3.4 数据流

```
LLM 决定调用 search_google_news(stock_code, stock_name)
  → is_enabled() 检查（GOOGLE_NEWS_COLLECTOR_URL 是否配置）
  → query = f"{stock_name} {stock_code}"
  → POST {collector_service}/jobs {"backend":"google_news","query":query,"params":{"max_results":5,"days":7}}
  → 轮询 GET {collector_service}/jobs/{job_id}，2s 间隔，总预算 40s（GOOGLE_NEWS_COLLECT_TIMEOUT_SEC 可配）
  → done: 截断正文到 500 字符 → 组装 SearchResponse/SearchResult
  → _persist_news_response 写入 NewsIntel（dimension="latest_news"，与 search_stock_news 相同维度、共用去重）
  → 返回给 LLM：{query, provider, success, results_count, results:[{title,snippet,url,source,published_date}]}
```

失败路径（collector-service 不可达 / 轮询超时 / job 明确 failed）统一收敛为 `{"success": False, "error": "<原因文案>"}`，不区分错误类型给 LLM（区分仅存在于日志里，便于运维排查），不阻塞 agent 分析主流程。

## 4. 配置

`.env.example` 新增（默认注释掉，未配置=禁用，符合"不配置也可运行，配置后增强能力"原则）：

```bash
# Google News collector (newsgrab collector-service) — HTTP integration, optional
# GOOGLE_NEWS_COLLECTOR_URL=http://localhost:8001
# GOOGLE_NEWS_COLLECT_TIMEOUT_SEC=40
```

读取方式：`google_news_collector_client.py` 内部 `os.environ.get(...)`，不进入中心化的 `src/config.py::Config` dataclass——与已废弃分支的 `plugins/google_news_collector/config.py` 同样的"独立小模块、一个可 patch 的入口"惯例。

## 5. 错误处理

- `GOOGLE_NEWS_COLLECTOR_URL` 未配置 → tool 直接返回 `{"error": "..."}`，不发起任何网络请求。
- collector-service 完全不可达（连接失败/DNS 失败等）→ `collect_google_news` 捕获异常，返回 `success=False`，tool 层原样透传为 `{"success": False, "error": "..."}`；不影响 agent 分析主流程。
- 提交成功但轮询超过 `GOOGLE_NEWS_COLLECT_TIMEOUT_SEC` 仍未见 `done`/`failed` → 同样收敛为 `success=False` 的降级返回，不重试、不阻塞。
- collector-service 明确返回 `status=failed` → 同样收敛，`error_message` 使用 collector-service 返回的 `error` 字段（如果有）。
- 任何非预期异常都在 `_handle_search_google_news` 外层再兜底一层 `try/except`，确保 tool handler 本身绝不抛出未捕获异常（双保险，`collect_google_news` 内部也不应该抛，但 handler 层保留这层防御与其它两个 tool 的写法一致）。

## 6. 测试

- `tests/test_google_news_collector_client.py`：mock `requests.post`/`requests.get`（沿用 `test_finnhub_fetcher.py`/已废弃分支 `test_browserless_client.py` 的 mock 风格），覆盖：
  - `is_enabled()` 在环境变量设置/未设置两种情况下的返回值
  - 提交成功 + 轮询一次即 `done` → 返回值组装正确（含 500 字符截断）
  - 轮询若干次后收到 `failed` → `success=False` 且 `error_message` 来自返回体
  - 轮询直到 deadline 耗尽仍未结束 → `success=False`，文案含 "timed out"
  - `POST /jobs` 直接连接失败（collector-service 不可达）→ `success=False`，不抛异常
- `tests/test_search_google_news_tool.py`：mock `google_news_collector_client.collect_google_news`（沿用已废弃分支 `test_search_google_news_tool.py` 的 mock 风格），覆盖：
  - 未启用时返回 `{"error": ...}`，不调用 `collect_google_news`，不调用 `_persist_news_response`
  - 成功时返回结构正确且 `_persist_news_response` 被正确调用
  - `collect_google_news` 抛出异常（未预期错误）时被外层 `try/except` 兜住，返回 `{"success": False, "error": ...}`
  - `collect_google_news` 返回 `success=False`（正常降级路径）时不调用 `_persist_news_response`
- 不在 CI 中起真实 collector-service 容器（三方/网络依赖，按 AGENTS.md 归为观测项而非阻断项）。

## 7. 明确不做的事

- 不改动 newsgrab（`playwright-service`/`collector-service`）任何代码——这是纯客户端集成。
- 不把 `search_google_news` 加入 `search_comprehensive_intel` 的 `self._providers` 自动轮询——沿用已废弃分支的结论，避免拖慢批量分析。
- 不做"决策链"/报告结构层面的新逻辑——采集到的新闻仍然只是写入 `NewsIntel`（`dimension="latest_news"`）+ 作为 tool 返回结果进入 agent 对话上下文，和现有两个 search tool 完全一致的消费方式，不新增下游处理。
- 不在本设计范围内验证"真实网络环境下端到端拿到真实 Google News 数据"——这是实现完成后的独立验证步骤，且已知在当前开发沙盒里 Docker 容器无法访问外网（newsgrab 侧 Task 10 已确认，非代码缺陷），需要另一个有真实公网出口的环境才能验证。

## 8. 未决问题（留给实现计划阶段细化）

- `GOOGLE_NEWS_COLLECT_TIMEOUT_SEC` 默认 40s 是否需要在部署文档里额外提醒：agent 运行时分配给单个 tool 调用的动态超时预算（`timeout_seconds`，见 `src/agent/executor.py`/`orchestrator.py`）需要大于这个值，否则会被外层 `ThreadPoolExecutor` 提前 kill，本设计的优雅降级逻辑没有机会执行——实现计划阶段需要确认是否要在 README/部署文档里加这条提醒。
- `docs/CHANGELOG.md` 具体落哪一行文案，留给实现计划阶段按 AGENTS.md 的扁平格式（`- [新功能] 描述`）拟定。
