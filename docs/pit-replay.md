# DSA v2 阶段 0A：自动 PIT 回放（研究原型）

`research/prototype/pit_replay/` 是隔离的日频研究回放器。它不会调用 LLM、NewsGrab 或下单接口，也不连接产品 API、Web、Desktop 或数据库。

## 运行

```bash
python -m research.prototype.pit_replay.runner \
  --stocks 600519,300750 \
  --start 2024-01-02 \
  --end 2024-03-01 \
  --decision-cutoff 15:30:00+08:00 \
  --output-dir research/local/pit_replay
```

默认策略 `pit-baseline-momentum20-v1` 只用于验证回放管线：20 个交易日收益率不低于 5% 时给出 50% 的研究目标仓位，低于 -5% 时退出，其余时维持前一目标。它不改变 DSA v0.1 默认规则，也不等同于已冻结的 v0.2 T/C/R 规则。

命令只通过 DSA 的 `DataFetcherManager` 获取日线。它会额外请求 120 个日历日用于预热，但仅在 `--start` 至 `--end` 内形成决策和评价 NAV。

## PIT 时间边界

- t 日只在 `decision_cutoff` 不早于收盘时形成目标；成交固定在 t+1 交易日开盘。
- 带时区精确发布时间的公告只在 `published_at <= decision_cutoff` 时可见。
- 只有发布日期的公告从后一个决策交易日才可见；同日一律排除。
- 没有可解析发布时间的公告会标记 `unavailable_pit_metadata` 并排除，不表示“没有公告”。

可用 `--disclosures-file PATH` 传入 JSONL 公告/基本面可得性元数据。每行至少包含 `symbol` 和 `kind`，并包含二选一的 `published_at`（ISO 8601，带时区）或 `published_date`（`YYYY-MM-DD`）。不传该文件时，manifest 会明确记录 `disclosure_availability=source_absent`。

## 审计产物与证据边界

每次运行在输出目录下新建不可覆盖的 `pit-replay-<id>/`，保存：

- `manifest.json`、`checksums.json`；
- `input_snapshots.jsonl`、`states.jsonl`、`targets.jsonl`；
- `orders.jsonl`、`fills.jsonl`、`nav.jsonl`。

当前统一日线不保存历史版本、复权因子或公司行动。因此从真实 DSA 数据源得到的运行始终标记为 `adjusted_price_simulation`，只能证明自动化、时间边界和审计链路；不能称为精确现金/股数账本或正式成本后绩效结论。缺少 t+1 价格、口径不一致或审计文件不完整时，应停止正式比较。
