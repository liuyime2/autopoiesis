# RSI 状态复核：2026-10-12

审查代码基线：`b1f118916ff926e93d553e34875a4e79a9bbad11`，主工作树审查开始时干净。
日期与交易会话以 America/Detroit 为准。本次只核对代码、真实 paper 记录、服务与远端 CI；没有下单、重启服务或调整风险限制。

## 可重复检查

- `make doctor`：0 failure、7 warning；paper endpoint、broker、Ollama、daemon 正常。
- `make benchmark`：退出 1，因目标不满足；20/60 有记录交易日、2326 cycles。
- `make daily-report DATE=2026-10-12`：266 cycles，0 cycle errors，0 submitted orders，0 LLM overrides。
- `make check`：在 lint 失败，`tools/check_research_tape.py:64` 的 `provenance` 未使用（RUF059）；未因此运行后续门禁。
- 为区分 lint 故障与行为回归，另行执行 `make type` 和 `make test`；type 为 48 source files 零问题，test 为 1323 passed，230.48s。该本机结果不能覆盖下文已复现的新克隆失败。
- GitHub Actions 最新源码 SHA 与基线一致：CI run `38307341195` 的 Python 3.10/3.11/3.12 均在 lint 失败；Pages run `38307341197` build/deploy 成功。Pages 成功不能证明代码 CI 成功。

远端证据：[代码 CI](https://github.com/liuyime2/autopoiesis/actions/runs/38307341195)、[Pages](https://github.com/liuyime2/autopoiesis/actions/runs/38307341197)。

## 运行与交易

daemon 为 active/running，最新一次启动在收盘后 16:55 EDT。heartbeat 表示 market closed / maintenance only；broker 正常，重启后 `last_model_ok=null` 表示还没运行模型决策，不能当成模型失败。

今天 09:35—16:00 共 266 cycles：264 HOLD、2 BUY；256 SKIPPED、10 REJECTED；205 LLM、61 deterministic fallback；没有成交或提交订单。Guardian 拒绝包含超过单标的限额以及行情报价过期，不能通过降低限制来增加成交。

历史保留记录有 97 次提交、97 次确认、157 股成交，未检测到 Guardian bypass。broker 当前 6 个持仓、0 个 open orders。以上是 paper 行为证据，不是实盘盈利。

## 盈利与决策

本次 benchmark 的诊断结果：realized +757.95、unrealized +89.59、假定成本 35.82，净 paper PnL +811.72；以各策略 peak exposure 之和 45233.90 为分母，agent +1.79%，SPY 同报告窗口 +3.32%，超额 -1.53 个百分点。6/6 可比较策略落后于各自同期基准。

资本分母仍不符合计划要求的同步组合资金/权益合同；净收益百分比只能当诊断。累计闭合 lot 盈利也不是今天的新增盈利，owner exit gain 不属于 agent。

137 个配对 LLM 决策、74 个 override，其中 45 个单股 24h probe 优于规则，不构成等预算组合对照。2326 决策有 1138 条早于模型 provenance；1255 个 LLM 决策中 408 已评分、737 等待 outcome。Brier 0.344，高于恒定 0.5 预测的 0.25；按决策日期调整的基准为 0.232，模型置信度的增量尚未证明。

## 与执行计划的差距

### 1. 已修复但没有下一场真实交易验证：symbol 路由

`b1f1189` 记录今天 266 cycles 中 238 次将策略匹配到其不覆盖的 symbol，约 89%。修复让两条生产决策路径传入 snapshot.symbol 并在选择前过滤。部署发生在收盘后，今天的 cycle 不能验证修复后的有效样本、paper 订单或持仓处置。

验收：下一市场会话逐 cycle 检查 strategy.symbols 包含 snapshot.symbol；无覆盖时明确 abstain；保证 probation 计数和结果按正确标的归因；不以强迫成交为验收目标。

### 1a. CI 还有被 lint 遮挡的新克隆失败

在 `/tmp/autopoiesis-audit-rpUlc4/tree` 克隆当前提交，使用独立 XDG_CONFIG_HOME/XDG_STATE_HOME 与空 journal 路径，运行：

```sh
make test TEST_ARGS='tests/autopoiesis/test_provenance_unified.py::test_the_published_record_contains_both_new_sections -q'
```

结果 1 FAILED：第 117 行要求输出 `status:`，但新克隆没有 heartbeat，工具正确输出 `(no heartbeat on this host)`。该测试依赖本机 daemon/runtime；本机通过不能证明新环境可用。修 lint 后 CI 仍会遇到这项失败。应使用明确标注的隔离 fixture 验证有/无 heartbeat 两种合同，禁止把生产账号和 journal 作为默认单元测试输入。

### 2. 预注册存在，但没有成为 research 的强制入口

`Makefile:155` 的 research-search 不传 spec；research-run 也没有调用 register/verify/check-tape。`tools/research.service.in:55` 使用这个不带 spec 的入口。今天定时任务仍搜索 134456 bars、十年合并 SPY 缓存，并耗时约 1h47m。服务退出 0 仅证明脚本结束，不能说明按声明实验执行。

driver.main 即便收到 spec，目前也只读日期过滤，没有验证注册状态、没有在该路径调用 check_tape，也没有保证 spec 的 instrument/rule/metric/family 与执行搜索一致。声明规则不控制实际候选生成。

默认注册目录计算为 `src/autopoiesis/runtime/autopoiesis/preregistered`；实测该目录有 2 个文件，预期顶层 runtime 下目录不存在。必须修正运行时目录与安装环境的配置合同。

验收：一个必经入口验证不可变 spec、数据及窗口、symbol/interval、候选集、成本、预算、holdout；不满足就拒绝；定时任务使用相同入口并记录 spec ID、data hash、版本与阶段结果。

### 3. 台账和 provenance 不一致

runtime research ledger 有 143 行，88 INSUFFICIENT、44 OVERFIT、11 INCONCLUSIVE_NO_FOLDS，0 PASS；99 行没有 dataset_fingerprint，应保留并标注 legacy/不可复现，不能补造。

`tools/provenance.py:111` 读取 fingerprint，但实际 ledger 用 dataset_fingerprint，因此真实指纹被漏读。相关测试用 fingerprint 构造记录，未覆盖生产写入合同。`tools/provenance.py:153` 将无 stdout 的 git diff --quiet 经 _run 转成 unknown，然后报告 dirty=yes；没有按退出码判断干净状态。

验收：生产写出的 trial 直接用于 provenance 合同测试；真实台账最新指纹可见；tracked/untracked 状态定义明确并按返回码检查；每次实验携带 spec/data/commit/config identity。

### 4. RSI 已有演化动作，尚无递归研究改进证据

今天 journal 有 19 CURRICULUM_PROPOSED 及相应准入审查事件。doctor 显示 250 候选来自 248 tasks，只有 1 task 搜索过，249 个无法说明促成候选的 observation；champion=NONE。说明候选生成、反思、准入和生命周期有运行，但尚未证实依据失败证据改善研究方法。

253 条策略 lineage 中 27 条链不完整，229 条样本不足、15 条未筛选；存在可选择但没有 evaluation evidence 的策略。paper 探针、decision screen PASS 与研究 alpha PASS 必须分开，不能当成通过 holdout。

没有找到计划要求的 research episode control/treatment 记录。需先冻结研究基线和准入合同，再以同数据、预算和风险限制比较 research efficiency 与独立 holdout 结果。

### 5. 基础设施实现进展不能写成全部部署完成

替换订单链恢复已新增；journal snapshot/restore 已在本机隔离目录比较真实账本数据。但没有持续异地主机备份部署证据。关机预算已改为派生 630s；这是等待上界，不是取消机制，curriculum/decision 同步调用仍需可中断边界。

doctor 仍报告 journal 旧 generations 已被轮转清除：校准、链与 champion 是保留窗口上的部分记录。需要明确保存期限、完整历史、备份调度、远端完整性与恢复演练。

## 下一轮优先级与验收

1. 修复 lint 与已复现的新克隆测试失败，隔离默认单测的生产状态依赖，再完成本地 check 与远端三版本 CI；不要把 Pages/release 成功当成行为门禁通过。
2. 验证已部署的 symbol 修复；统计有效 cycle、无覆盖 abstain、probation 公平分配、真实订单意图和 Guardian 结果。排查旧错误标的样本对策略退休/复活/评分的污染，保留原始记录。
3. 将预注册、数据质量、spec 与实际搜索的一致性变成强制入口；修默认 registry 和 provenance 字段合同。先得到一轮可信负结果，再扩大搜索。
4. 修齐收益与风险合同：同步组合权益/资本、现金/敞口、owner 与 agent 分开、真实 fills、成本压力和 paired baseline。补齐 outcome 数据与模型校准。
5. 用有来源的失败 lesson 指导不同可证伪假设，设置停止预算；再开展冻结 control 与 RSI treatment。禁止无信息重复候选占用 probation 和算力。
6. 部署持续备份与定期恢复演练，优化高耗时 journal 重复扫描和可取消调用；修正研究服务的 last-success/数据新鲜度/实验合规性展示。
7. 独立 holdout → shadow → 单候选有期限 paper probation → 连续完整 60 个有效交易日。当前 20 个有记录交易日不能直接算作连续合格窗口，不能承诺再过 40 天就达标。

当前结论：paper 系统运行、风险保护有真实证据、演化机制在产生动作；可信 alpha 为 0，组合超额为负，受控 RSI 增益未证明。尚不能宣称系统开始正确盈利或达到 RSI 目标。

---

## 2026-10-12 收盘后补充：污染范围量化与常设门禁

以下数字由命令实测得到，不是从上文推断。命令与输出记录在案。

### 污染起点与总量

保留窗口内 2326 条 cycle，1602 条带 `strategy_id`，其中 **862 条（53.8%）把策略配给了它不覆盖的标的**。按日期分布：

```
2026-06-11 .. 2026-10-07   15 个交易日   0 条错配   (0.0%)
2026-10-08                 395 cycles   329 错配   (83.3%)
2026-10-09                 328 cycles   295 错配   (89.9%)
2026-10-12                 266 cycles   238 错配   (89.5%)
```

**起点是 2026-10-08，之前 15 个交易日零错配。** 污染集中在最近三个交易日，共 862 条，涉及 7 个策略。

### 下游污染：7 个 lifecycle 判决 + 8 个 screen 裁决

污染期（>= 10-08）共有 463 条 `STRATEGY_LIFECYCLE_UPDATED(applied)`，其中 6 条改变了生命周期状态，7 条相关判决如下：

| 时间 | 策略 | 判决 | 错配样本 | 理由 |
|---|---|---|---|---|
| 10-08 15:31 | `tiny-fixed-size-001` | RETIRED | 0/48（**干净**，均在污染前） | offline validation rejected ... |
| 10-08 15:56 | `trend-follow-20260727-001` | RETIRED | 0/59（20 条污染期样本） | 15 scored, 2 correct |
| 10-12 14:35 | `fixed-size-probe-0001` | PAUSED | **102/180** | no exploration evidence |
| 10-12 15:03 | `fixed-size-buy-20261003-001` | PAUSED | **18/18（100%）** | probation produced no exploration |
| 10-12 17:44 | `trend-follow-20260818-001` | PAUSED | 0/13（13 条全在污染期） | probation produced no exploration |
| 10-12 19:03 | `trend-follow-20260721-001` | PAUSED | **9/13** | probation produced no exploration |
| 10-12 19:22 | `trend-follow-20260722-001` | PAUSED | **8/13** | probation produced no exploration |
| 10-12 19:44 | `trend-follow-20260723-001` | PAUSED | **18/19** | probation produced no exploration |

污染期的 `OFFLINE_VALIDATION_COMPLETED` 共 201 条：193 INCONCLUSIVE、**6 PASS_SCREENED**、2 REJECT_POOR_DECISIONS。被屏最多的 `fixed-size-probe-0001` 有 102/180 条错配样本。

**限制**：journal 已轮转到 4 代，只覆盖保留窗口。上表的"干净/污染"只对可见记录的样本成立，不能追溯重跑更早的判决。

### 为什么这些判决不能直接撤销

`fixed-size-probe-0001` 被 PAUSED 的原因是"no exploration evidence"，而它 180 个周期里 102 个跑在错误标的上——它从未在 SPY 上被运行过。但手工把文件改回 `PROBATION` 正是这个项目明确移除的旁路（代码注释原文："hand-editing the files is exactly the bypass this project removed"）。判决记录在 journal 里，状态文件的 `lifecycle_reason` 会与 journal 不一致，且无法恢复被污染证据本该产生的新样本。**正确做法是等修复后的干净样本积累，让系统用自己的机制重新审议**（`_reconsider_paused` 需要 PAUSED 满 3 天 + screen PASS + 队列有空间）。

### 新增常设门禁

`tools/check_runtime_integrity.py` 增加第 6 条规则，由 `data-integrity` 门禁类执行：

- 每条 cycle 的 `decision.strategy_id` 必须覆盖 `snapshot.symbol`；
- 修复部署时刻以前（`MISMATCH_FIX_UTC = 2026-10-12T20:20:00+00:00`）的错配计入 cohort 并**打印数量**，不算失败；
- 该时刻以后的任何错配是**复发，直接 FAIL**。

边界取时刻而不是"当前数量"，因为时刻天然自限：它宽恕已写下的全部，不宽恕其后写下的任何一条，因此无法被放宽来豁免复发。当 journal 把 cohort 轮转出去，这一项和常量就可以删掉——打印出来的计数就是判断依据。

**这个缺陷存在了 4 天（10-08 至 10-12）而没有任何门禁发现它**，因为选择器按标的过滤从来不是被检查的不变量。门禁现在检查它。
