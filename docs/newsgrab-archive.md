# NewsGrab 前瞻新闻归档（DSA v2 阶段 0B）

`research/prototype/newsgrab_archive/` 是 DSA v2 的研究原型：它把 NewsGrab 的异步新闻采集结果写成 DSA 自己拥有的、不可覆盖的归档文件。它复用现有 `GOOGLE_NEWS_COLLECTOR_URL` 连接同一个可信 collector；它不调用 LLM，不产生事件/情绪因子，不改动默认规则，不改动当次 API/Web/Desktop 报告，也绝不会下单。

NewsGrab 是无状态采集服务，DSA 归档才是未来 PIT 回放的证据库。接入日之前没有已归档新闻，必须视为不可恢复的 PIT 缺口，不能因 NewsGrab 今天能搜索到旧文章而补写历史结论。

## 安全与部署边界

NewsGrab 的 collector 没有应用层认证。沿用仓库既有的 [NewsGrab 联合部署说明](newsgrab-integration.md)，不要为方便测试把 collector 或 playwright 服务暴露至公网。阶段 0B 不新增 Docker Compose、网络或第二套 endpoint 配置。

先按既有部署文档启动可信 collector，并设置 `GOOGLE_NEWS_COLLECTOR_URL`；本归档 CLI 也允许用 `--base-url` 显式覆盖该值，便于隔离测试。

## 手动归档一轮查询

阶段 0B 不安装调度任务。应由受信任的内部任务在收盘后显式运行 CLI；下例从 DSA 容器加入的内部网络调用 collector：

```bash
docker compose -f docker/docker-compose.yml run --rm server \
  python -m research.prototype.newsgrab_archive.runner \
  --base-url http://newsgrab-collector:8000 \
  --archive-root data/newsgrab_archive \
  --query '贵州茅台 600519' --scope company \
  --language zh-CN --region CN --max-results 10 --days 1
```

不设置 `GOOGLE_NEWS_COLLECTOR_URL` 时，CLI 要求显式传入 `--base-url`。默认归档位置为 `data/newsgrab_archive/`，在 DSA Docker 环境中由既有 `data` 挂载持久化。

为避免以宽时间窗抓取后伪装成前瞻资料，`--days` 固定为 `1`；CLI 和数据契约都会拒绝其他值。即使采集结果的发布时间早于归档时点，`archived_at` 仍限制它不能被用于该时点之前的 PIT 回放。

每次运行创建唯一目录：

```text
data/newsgrab_archive/YYYY-MM-DD/<run-id>/
  manifest.json
  articles.jsonl
  errors.jsonl
```

`manifest.json` 记录请求、NewsGrab job id、提交/完成/归档时间、计数和两个 JSONL 文件的 SHA-256；`manifest.sha256` 再记录 manifest 本身的 SHA-256。`articles.jsonl` 保存标准字段与原始 NewsGrab article；`errors.jsonl` 保存失效文章、协议失败或 job 失败。完成后所有文件和运行目录都会改为本地只读，目录已存在即失败，绝不覆盖先前证据。此措施防止正常运行路径改写已归档文件；如需监管级 WORM/对象锁，必须将目录复制到具备该保证的外部存储，阶段 0B 不会伪称提供该能力。

## PIT 语义

归档条目的 `published_at_precision` 可能是 `exact`、`date_only`、`missing` 或 `invalid`。只有未来某个回放截点同时满足以下条件的文章，才可用于严格 PIT：

```text
published_at is exact
published_at <= decision_cutoff
archived_at <= decision_cutoff
```

`strict_pit_status` 是**归档当刻**的保守初步分类；后续 PIT 消费方仍须针对实际 `decision_cutoff` 再判断。只有日期的 `published_date` 永远不会被伪造成当日发布时间，必须走明确的下一交易日规则；缺失、解析失败、截点后发布或截点后归档均不可用于严格 PIT。失败运行也会留下 manifest 和 error 记录，不能被解释为“当日没有新闻”。

## 验证与回滚

离线验证使用本地 HTTP fixture，覆盖 job 提交/轮询、服务失败、协议错误、时间精度、不可覆盖归档和 checksum；不会访问真实 Google News 或 NewsGrab 服务。

本次实现验证记录（2026-07-28）：

- `python -m pytest tests/test_newsgrab_archive_*.py -q`：14 passed；
- `python -m py_compile research/prototype/newsgrab_archive/*.py`：通过；

本阶段没有执行真实 NewsGrab 或 Docker 网络调用；测试使用本地 HTTP fixture，避免把网络可用性误写成归档逻辑证据。

回滚不需要迁移现有策略或数据库：移除 `research/prototype/newsgrab_archive/` 即可；既有实时 NewsGrab 集成保持不变。已归档文件是审计证据，除非有明确数据保留授权，不应作为回滚步骤删除。
