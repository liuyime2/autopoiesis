
# 🧠 Quant Voyager 学习型守护进程

## ✅ 已设置

1. **小时监控** - 使用 cron 每小时运行 monitor.sh
2. **Daemon 持续运行** - agent 已在后台持续运行

## 🎯 系统设计（类似 React 的学习-反馈循环）

### 市场开盘时
- **快速反应** - 每 4 分钟一个周期，实时决策
- **持续学习** - 每 10 个周期进行一次 reflection
- **策略进化** - curriculum agent 提出新策略
- **知识积累** - 从经验中生成 LESSON artifacts

### 市场收盘时
- **维护模式** - 仅进行反思和评估
- **证据更新** - 每小时更新 broker 证据
- **状态监控** - 检查学习进度

## 📊 当前状态

- Daemon: RUNNING (pid 2341463)
- Cycle count: 53
- Error count: 0
- Strategies: 11 (2 ACTIVE, 7 PROBATION, 1 RETIRED, 1 BASELINE)
- Knowledge: 3 ACCEPTED LESSONS
- Account PnL: +0.54%

## 🧬 持续优化循环

```
市场数据 → 决策引擎 → 执行 → Journal → Reflect → Knowledge → 更好的决策
                                    ↑                                    ↓
                              PnL Evidence ← Broker ←────────────────────┘
```
