# 🤖 Quant Voyager 自动审查与修复系统

## 📋 概述

统一的审查 / 自动修复 / 自动唤醒入口：

| 文件 | 用途 |
|------|------|
| `auto_reviewer.py` | 主入口：7 步审查 + 内嵌策略启用修复 |
| `auto-fix.sh`      | 离线修复：策略 enabled/lifecycle、max_position_value、stale PID |
| `check-market-open.sh` | 用真实 Alpaca clock 写 `runtime/min_agent/market-state.json` |
| `monitor.sh`       | 旧版监控脚本（保留供参考，cron 不再调用） |

> 之前的 `auto-review.sh` 与 `auto_reviewer.py` 内容重复，已整合到 `auto_reviewer.py`，shell 版本已删除。

---

## 🔍 审查项目（7 步）

1. 守护进程：状态 / 错误计数（high → 自动重启候选）
2. 经纪人对账：matched / extra_broker_orders（high → 先 ingest-evidence 再判断）
3. 策略库：核心 4 个策略 enabled 与 lifecycle（medium → auto-fix）
4. 知识库：lesson 数量（low → 仅记录）
5. PnL 证据：submitted_orders / pnl_status / linked / unlinked / closed_lots
6. 测试套件：pytest tests/min_agent
7. 自动修复：仅对已确认的问题机械修复，不绕过 Guardian / 不改安全参数

---

## 🔧 自动修复范围（auto-fix.sh）

仅以下三类机械修复，且全部通过 `json.load`/`json.dump` 而非 sed 完成：

1. core 策略 `enabled=false` 或 `lifecycle=PAUSED` → `enabled=true, lifecycle=PROBATION`
2. core 策略 `max_position_value < 1000` → 提升到 5000.0
3. `runtime/min_agent/daemon.pid` 指向已退出进程 → 删除 stale 文件

只有发生改动时才会跑回归 `pytest`（节省 ~30s 无操作开销）。

---

## ⏰ 推荐计划（与当前 cron 一致）

| 频率 | 命令 | Cron |
|------|------|------|
| 每小时 :07 | `python auto_reviewer.py` | 13284297 |
| 每 4 小时 :23 | `python auto_reviewer.py` + 完整 pytest | 6f0c6586 |
| 工作日 13:29 UTC（开盘前 1 分钟） | `bash check-market-open.sh` + `bash auto-fix.sh` | af862ba3 |

cron 是 session-only，7 天后自动过期；改 durable 需要明确请求。

---

## 📊 输出

- `reviews/review-YYYYMMDD-HHMMSS.json` — 每次审查的结构化报告（保留 7 天）
- `auto-fix.log` — auto-fix.sh 写入的修复日志
- `market-check.log` — 开盘检查日志
- `runtime/min_agent/market-state.json` — 最新 Alpaca 市场状态缓存

---

## 🎯 一次性使用

```bash
cd /localscratch/liuyime2/QuantGroup
python auto_reviewer.py        # 全量审查 + 自动修复（核心策略）
bash auto-fix.sh               # 仅修复
bash check-market-open.sh      # 仅检查市场状态
```

---

## 与项目原则的对齐

- **Real Data Only**：所有 PnL / reconcile / status 数据来自 Alpaca paper API 与 daemon JSONL。
- **paper-only**：所有命令强制 `ALPACA_BASE_URL=https://paper-api.alpaca.markets`。
- **Surgical Changes**：自动修复只覆盖三类机械问题，不调 Guardian、不改安全阈值。
- **优化机制优先**：检测到问题先报告、再调既有 fix 脚本，不在 cron prompt 中临时改 skill 绕过机制。
