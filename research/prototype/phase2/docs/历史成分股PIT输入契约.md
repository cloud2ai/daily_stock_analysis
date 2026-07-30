# 阶段 2 历史成分股 PIT 输入契约

历史成分股证据采用三层不可变模型。`research/prototype/phase2/universe.py` 当前读取第三层的严格解析结果输入；后续真实数据接入会由前两层确定性生成它。系统不下载数据、不指定数据供应商，也不把当前成分股写成历史证据。

## 三层证据模型

1. `membership_observations`：原始观察，例如 JoinQuant 对某个 `as_of` 返回的成员快照。保存 provider、请求/响应哈希和抓取时间，证据等级为 `provider_historical_snapshot`。
2. `membership_evidence`：原始证据附件，例如中证指数调整公告。保存公告时间、调入/调出、生效日、来源引用和内容哈希，证据等级可为 `official_timestamped_announcement`。
3. `resolved_membership_intervals`：由冻结解析规则从前两层生成的成员区间。保存所用 `evidence_id`、规则版本、输入哈希和 `pit_status`；只有满足严格时间要求的结果才是 `strict_pit`。

公告不是给既有 JoinQuant 行“补写”一个时间字段。它是新的证据记录；解析器据此生成新的结果版本。这样旧回放仍能重建当时实际使用的低等级快照，也能表达一份公告支持多只股票、与快照冲突或后续解析修正。

## 当前严格解析结果输入

输入根目录位于被忽略的 `research/prototype/phase2/raw_data/` 下，必须包含：

- `historical_index_membership.csv`
- `historical_index_membership_manifest.json`

CSV 使用严格字段：

```csv
index_code,symbol,announced_at,effective_from,effective_to,source_name,source_reference
000300.XSHG,600519.XSHG,2023-12-08T18:00:00+08:00,2023-12-11,2024-06-14,csi,archive://csi300/2023-12-adjustment
```

- `announced_at` 是第三层解析结果所引用的最早可得公告时间，必须带时区；只有它不晚于 t 日决策 cutoff 的记录才可见。
- `effective_from` 和 `effective_to` 均为必填日期，且结束日期包含在区间内。归档者需在有限回放窗结束时关闭仍生效的区间。
- 同一指数、同一股票的原始区间不得重叠。
- `source_reference` 指向可说明该条成分变化的归档引用；它不是运行时下载地址。后续解析结果还必须能回指第一、二层的具体 `evidence_id`。

manifest 的最小结构：

```json
{
  "schema_version": "phase2-historical-universe-input-v1",
  "csv_sha256": "64 位小写 SHA-256",
  "retrieved_at": "2026-07-30T09:00:00+08:00",
  "source_archive_sha256": "64 位小写 SHA-256",
  "source_archive_reference": "archive://csi300/constituent-adjustments",
  "timezone": "Asia/Shanghai"
}
```

`csv_sha256` 校验第三层解析结果未被改写；`source_archive_sha256` 记录外部归档原件。两者都不能替代 `announced_at`：没有可证明发布时间的资料不能成为严格 PIT 证据。

## 逐日可见性

加载器按 XSHG 交易日遍历每个生效区间。若公告在生效日收盘 cutoff 后发布，该股票从第一个满足 `decision_cutoff >= announced_at` 的后续交易日才可进入股票池。

例如，若成员自 2024-01-02 生效，但 `announced_at` 为 `2024-01-02T16:00:00+08:00`，在 `15:30` 决策设置下它不属于 1 月 2 日的可见成员，而可从下一交易日开始使用。

## 失败关闭

以下情况均拒绝输入或阻止后续因子/模型计算：CSV 哈希不符、manifest 无效、时间戳无时区、日期无效、区间重叠、指数代码不一致，以及任一请求决策日没有可见成员。

通过后，`UniverseGateRecord` 和 `build_universe_gate_audit_payload` 保存每个决策日的成员、cutoff、状态、输入哈希、来源归档哈希和检索时间。只有 `evidence_quality="strict_pit"` 的完整载荷可以进入阶段 2 因子选择。现有阶段 1A 的 `effective_date_unverified` 输入不会被自动升级。
