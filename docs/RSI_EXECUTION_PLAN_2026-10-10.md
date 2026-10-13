# Autopoiesis RSI 执行计划：发现 alpha、形成决策、用证据改进

日期：2026-10-10（America/Detroit）
计划基线：提交 `c32f41176cc47ab46b017d705edaec45aab0cc8e`。
范围：研究和判断可交易信号、决策质量、递归改进研究过程，以及支撑这些工作的可靠运行基础设施。本文件是计划，不表示下述目标已实现。

## 执行进度记录（2026-10-11）

已完成并有证据的基础设施工作：隔离工作树中的 watchdog stale-heartbeat 恢复修复已提交（`ed51c55`），当时完整测试为 1195 passed / 3 skipped、verify 为 52 类零失败；优雅停机审计已记录为独立结论（`505449d`），确认 30 秒 SIGKILL 记录无法归因具体阶段，且 Alpaca 请求没有有限 timeout，因此不能据此直接加大 `TimeoutStopSec`；只读 paper broker 延迟样本已真实执行一次：SDK 3.2.0、20 次 clock + 20 次 account、40 次请求成功、0 失败，合并 p50 25.036ms / p95 27.248ms / max 90.247ms。采样器、精确 SDK pin、此次测试/verify 和结果限制见 `docs/superpowers/plans/2026-10-10-broker-latency-baseline.md`：最终 make check 为 1209 passed / 3 skipped，verify 为 52 类 0 失败、1906 次测试执行，mypy 45 个源文件零问题。

2026-10-11 又完成一段 paper 订单与账本安全基础：SDK 请求统一经过固定 timeout/禁重试的 client；提交前 fsync 写入 intent，提交响应不明时全局阻止新订单并按精确 client ID 恢复；兼容旧 ERROR 记录；归档 manifest 对历史做连续性校验并提供显式操作员基线审计入口；部分成交以累计快照幂等投影；恢复 runbook 与 CLI/Makefile 路径已加入。证据：`make verify` 52/52 类通过、1959 次测试执行、0 失败；lint 与 mypy（47 源文件）通过。该实现遇到 `replaced` 状态会安全地持续阻断，但尚未跟踪 Alpaca replacement chain；这是未完成项，不能称订单生命周期全部闭环。所有这些只证明离线机制和 fake broker 行为，不证明线上 broker 故障恢复、alpha 或盈利。

Alpha 搜索可复现性继续推进：当前 driver 之前按策略、symbol、参数和 bar 数去重，未核对内容；实测新增的同长度价格变更测试得到 `repeats=2`，错误地把两条不同价格序列当作旧数据。另一个基线问题是 `search()` 无条件把任意输入标为“Alpaca historical bars; not synthetic”，连合成测试序列也如此。修复要求每个调用显式提供来源，真实 cache 运行传来源元数据，数据集 fingerprint 对有序 timestamp/price 做 SHA-256；旧 ledger 缺 fingerprint 时重跑一次。计划与完整验证结果记录在 `docs/superpowers/plans/2026-10-11-research-dataset-identity.md`。本阶段不会把合成单测当成真实 alpha 证据。

此数据身份修改的最终 gate 结果：research-driver 21/21 测试，`make verify` 52/52 类、1,971 次执行、0 失败。只验证了防止数据集误去重及来源误标，不代表已完成真实数据 alpha search 或任何策略晋升。

随后补齐了可执行 research fetch loop：Makefile 有显式 UTC 窗口的 `fetch-replay-bars`、离线 `research-search` 和顺序 `research-run`；scheduled research unit 也通过同一 Makefile 入口。只读真实 fetch 得到 24,237 根 SPY 5 分钟 bars（2026-04-10 至 2026-10-09），dataset hash 为 `sha256:9137cff68e231f520d1b8ceaa7f3cb8592398162d22088ea0ee508afcd70296b`。11 个既有候选的 verdict 是 4 OVERFIT、7 INSUFFICIENT、0 PASS；详细数据与限制见 `docs/evidence/alpha-search-2026-10-11.md`。搜索首轮约 2 分 53 秒；同 fingerprint 的完整重复搜索现在不再跑 backtest，实测 5.461 秒、11 repeats、0 backtests，约快 31 倍。负结果不用于准入，也没有放宽研究门槛。此结果仅覆盖单一近期窗口和旧策略族，不是最终 holdout，也没有证明 alpha、LLM 决策增益、RSI treatment 优于 control 或 60 日目标。

该 alpha 数据入口及重复运行优化最终通过 `make check`（1,253 passed / 3 skipped）与 `make verify`（52/52 类、1,979 次执行、0 失败）；mypy 覆盖 47 个源文件。真实 paper 账户只用于历史行情只读抓取，没有下单。

这些交付推进了阶段 A 的 watchdog、请求边界、歧义订单保护和部分账本恢复；异地备份/恢复演练、replacement chain、SIGTERM 真正可中断边界、统一 provenance、alpha、决策质量和 RSI control/treatment 仍未完成。当前 paper 观察窗仍需满足原定连续 60 个有效交易日；不能把测试/verify 的绿色代替运行时、收益或 RSI 证据。

## 执行状态复核（2026-10-12 收盘后）

此前隔离工作已合入主分支，审查基线为 `b1f1189`。新增替换订单链恢复、本机隔离目录的真实账本恢复演练、派生关机预算（630s）、健康字段、研究预注册与窗口过滤。这些实现不表示持续异地备份、调用取消或完整 research/RSI 验收已完成。

本次实际命令：`make doctor` 为 0 failure/7 warning；今日 266 cycles、0 errors、0 submitted orders、0 LLM overrides。今天 238 次策略/标的不匹配已修代码，但收盘后部署，下一次开盘验证仍未完成。`make benchmark` 为 20/60 有记录交易日，诊断 agent +1.79% vs SPY +3.32%，净超额 -1.53 个百分点；资本分母仍需修复。最新 research ledger 143 行、0 PASS、99 行无 dataset fingerprint。

新的阻断项：主分支 `make check` 因 RUF059 失败，远端三版本 CI 同样在 lint 失败；隔离新克隆还复现了 provenance 测试依赖本机 heartbeat 的失败。research-search/定时任务未强制 spec 验证和数据门禁，今日仍搜索十年合并数据；registry 默认路径落在 src 内，provenance 的指纹字段与 trial 合同不一致。演化提案在运行，但 research control/treatment 仍未开始。旧阶段完成记录保留为历史，不能用来声称最新提交 CI 已通过。

完整差距、命令证据和下一轮验收见 `docs/evidence/rsi-status-audit-2026-10-12.md`。下一优先级为新克隆/CI → 正确标的周期验证 → 强制研究合同/provenance → 同步组合收益/决策对照 → 受控 RSI → shadow/有限 paper 与完整 60 日窗口。

## 夜间复核增量（2026-10-12，代码基线 49063c8）

上次的 lint、heartbeat 主机依赖测试、requests typed override 和 provenance 指纹字段问题已有提交修复；symbol 历史污染量化为 862/1602 有 strategy_id 的 cycles，新增复发门禁。registry 路径只有未提交修改，不算部署完成。research 必经入口和受污染策略的干净样本重新审议仍未完成。

本机工作树 `make check` 实测 lint/type（48 源文件）通过、1329 passed、315.80s；最新已提交源码的远端三版本 CI 仍失败。读取 job 日志并在 UTC 独立克隆复现，根因为 VIX 测试固定日期与未固定当前时间：缓存年龄跨过 4 天门槛，导致另一分支。下一修复是固定测试时钟，保留生产过期行情保护。本机绿色不能替代最新远端 CI。

研究台账仍 143 行/0 PASS，收盘后没有新的市场周期，盈利与 RSI 结论未改变。详细差距、复现与下一轮验收见 `docs/evidence/rsi-status-followup-2026-10-12.md`；历史记录保留而不覆写。

## 1. 问题、基线、成功标准、验证

### 问题

系统已有 paper 执行、Guardian、journal、反思、策略准入、研究搜索和 paper probation。现在缺的主要证据是：

1. 研究能否在可信数据上找到经样本外检验仍成立的 alpha；
2. 决策能否把信号、成本、风险和不确定性结合起来，并在相同约束下优于简单基线；
3. 多轮试验能否改善发现与决策方法，而非仅持续提出新策略；
4. 研究、交易、账本和报告能否在稳定、可复现、容易定位故障的环境中持续运行。

“有候选”“日志有正数”“CI 通过”“daemon active”都不能单独证明上述结果。

### 可测基线（2026-10-10 检查）

- GitHub 提交 `c32f411` 的 CI 在 Python 3.10、3.11、3.12 三个 job 通过。工作树另有未提交的站点、报告和 gate 相关文件；计划执行前应先界定这些工作的归属并保留，不把未提交代码当作已验收基线。
- paper daemon 与 Ollama 为 active/running；watchdog 最近成功。heartbeat 标记 `RUNNING`，当时累计 error_count=1、cycle_count=0、消息为 maintenance in progress。维护状态和交易 cycle 不能混为一项健康指标。
- 当前记录约 2060 cycles、19 个有记录的交易日，目标观察窗 60 日；benchmark 明确标记 19/60、NOT MET。
- 最新 benchmark：扣假定成本后 agent +1.81%，SPY 同窗口 +2.69%，落后 0.88 个百分点。资金分母仍是各策略 peak exposure 之和，不是同一时刻的组合峰值资本，故视为诊断值，须先修统一绩效合同。
- 最新 11 个研究候选没有通过：结果为 INSUFFICIENT 或 OVERFIT，PASS=0。一次真实 fetch→research→trial 闭环已运行；运行闭环证明研究入口能执行，不证明 alpha 存在。
- 2060 条决策约 52% 无模型归因。107 个可配对决策中的 42 个局部探针优于规则，是单股 24h 探针，不是组合净收益证据。
- 最近的 systemd 日志记有 daemon 两次停止超时并被 SIGKILL；report service 尚有旧失败状态；research 最近一次执行成功并已退出，timer 继续安排下一次任务。
- 最近历史成果：finite-number Guardian 检查、成本统一、混合缓存修复、journal 本机归档、只读面板六页来源代码以及真实研究闭环已有提交。异地备份、完整面板验收、稳定长期 paper 对照和发布站仍未完成。

这些数字来自当前 paper 记录及最近一次研究试验，不是新的冻结样本实验，也不应据此推断未来收益。

### 总体成功标准

只有下列链路全部有证据，才能称为“具备 RSI 实证闭环”：

```text
可复现数据
  → 预注册问题与基线
  → 大规模候选探索
  → 多重检验控制
  → 独立样本外验证
  → 风险/成本/容量校验
  → 影子运行
  → 有限 paper probation
  → 同口径评估
  → 记录失败与成功原因
  → 下一轮研究方案能据此改变
```

不能保证任何有限周期内一定发现盈利 alpha。可以验收的是研究结论可复现、没有数据泄漏、负面结果不丢失、决策按规则控制风险，并且系统只将通过预先定义门槛的变更送入下一阶段。

## 2. 名词和评估合同

| 名词 | 在本计划中的含义 | 不可替代成 |
|---|---|---|
| Alpha 假设 | 事前写明的可证伪规则：哪些可观测信息，在何时产生什么预测或仓位决定 | 回看历史后选出的最好曲线 |
| Alpha 候选 | 在开发数据上有足够交易数、扣成本后达到预先定义经济门槛的候选 | 一次回测胜过 SPY |
| 验证通过 | 在从未用于挑选候选的时间段、真实交易规则和多重检验门槛下，结果仍成立 | OOS 中某一段正收益 |
| 决策能力 | 在同一信息集、决策时点、capital/risk/cost/成交模型下，决策器优于简单规则，并改善风险调整后的结果 | LLM override 较多、解释听起来合理 |
| RSI 改进 | 后一轮搜索/选择/决策方法在预注册元指标上优于上一轮，且没有以风险、数据质量或评估门槛换分 | 策略数量增长或提示词改写 |

每项研究开始前生成一份冻结的 experiment spec：hypothesis、universe、bar size、可用信息截止时间、数据来源与 hash、时间切分、benchmark、成本和滑点压力、资本占用、候选总数、主要指标、通过/失败/不足规则。结束后只追加结果；不得按结果改指标后仍称同一试验。

所有 performance 指标须标注 observation count、交易日区间、真实 fill 数、未归因占比、gross/net、假定成本、capital basis、基准、风险暴露、置信区间及实验 ID。合法的零收益是 0；缺数据才是 UNKNOWN。

## 3. 阶段路线图

依赖顺序为 A → B → C → D → E；F 与 A–E 并行积累数据，但不能跳过 A–E。每个阶段是一项独立交付，不把未通过条件的阶段自动标成完成。

### A. 运行可靠性和证据源（先做）

**目标**：让每一条研究与交易结论来自可识别、完整、能恢复的数据和版本。

工作：

1. 将 runtime 数据、source commit、dirty-tree 状态、依赖、配置摘要（脱敏）、时间区间、来源与数据 hash 写入每次报告和 research trial。
2. 统一实时 broker 证据、journal history、成本和 benchmark 使用的事件合同。准确区分 account owner、agent、策略持仓及 broker 确认 fill。
3. 给 journal/archive 建立主机故障恢复方案和异地备份。校验后再清理旧副本；恢复后验证 event count、FIFO lots、策略状态和每日权益轨迹。
4. 将 heartbeat 拆为 daemon PID/liveness、最近成功 cycle、最近维护阶段、最新行情时间、broker/LLM 状态、错误计数与年龄。doctor 和 watchdog 发现卡死或“RUNNING 但无进展”。
5. 让 daemon 在 SIGTERM 时完成安全收尾；找出当前超过 30 秒停机并被 SIGKILL 的阻塞阶段。对 LLM/broker 请求设阶段超时、取消、清晰错误及恢复策略，不阻止 Guardian。
6. 分清周末 maintenance 和 market cycle；服务指标显示最近任务时间、最近成功时间、执行时长、峰值内存及失败次数。

**交付**：单一 snapshot/provenance 合同、备份与恢复 runbook、服务健康状态和定时任务结果。

**验收**：新环境按 Makefile 完成 install、check、type、offline smoke；GitHub matrix 通过。隔离故障注入能证明坏行情、断 broker、LLM timeout、损坏归档、daemon 卡死各自可检测并恢复。复原测试在超过热备份数量后，所有审计所需数据一致。绝不以强制恢复服务替代风险核查。

### B. Alpha 发现流水线

**目标**：更快地筛除没有信息量的候选，并保护最终检验集不被反复窥视。

工作：

1. 先选狭窄可解释的 universe 和周期，第一轮固定 SPY 5 分钟或日线之一；分别为价格/波动、市场状态、新闻/事件建立假设族，禁止混池后只汇报最好项。
2. 核实 bar/action split/dividend/market calendar/timezone、缺失数据、symbol、interval、来源和 point-in-time。基本面/新闻使用当时可得的发布日期和抓取时间；事后修订不可回填成当时已知。
3. 使用 chronological train/validation/untouched test；在训练窗内做 walk-forward。最终 test 只在筛选冻结后开启一次，开启后如再调参，就标为开发数据并建立新的未来 holdout。
4. 搜索按成本分层：超便宜 sanity/data checks → 向量化候选筛选 → walk-forward → 成本/滑点/延迟/交易频率压力 → 置信区间/多重检验 → 相关性与跨时期稳定性。
5. 所有候选、重复候选、失败和不足样本都写 ledger；实验 ID 纳入参数、universe、时间区间、bar schema 和 dataset fingerprint。不能只用 bars 数作为数据版本。
6. 先复用已声明的 numpy/pandas 研究依赖，避免研究额外依赖进入 production worker；将研究解释器、缓存、CPU/内存限制及 Makefile 入口明确化。

**候选门槛**：开发阶段要满足最小真实成交数和多段 walk-forward 的方向一致性；扣除预设往返成本、保守滑点及容量冲击后净收益达到预先声明门槛；风险、换手和回撤限制通过；多重检验修正后有统计支持。门槛由 B 阶段预注册，不在看到 test 之后设。

**验收**：对一份固定、真实行情快照重复运行，结果按 experiment ID 完全可复现；拆掉/打乱未来信息的对抗样例使泄漏测试失败；有单位测试确保筛选不会读取 holdout；每次运行明确列出探索的候选总数和全部 verdict。数据不足就输出 INSUFFICIENT，不进行策略准入。

**停止条件**：一族假设在预注册预算/样本上限内无候选通过就结案，保留失败台账并转向新假设。不能通过提高搜索量直到出现赢家来证明 alpha。

### C. “知道如何决策”的政策评估

**目标**：证明决策器何时行动、何时 abstain、何时缩小风险，并且相对明确的非 LLM 基线有增量。

同一条决策记录至少携带：decision timestamp、当时可用 market snapshot、特征与来源时间、rule/action、模型及版本、提示词版本、模型输出/结构化理由、confidence、Guardian 结果、下单意图、broker order/fill/cost、后续统一 horizon 的 outcome。

建立严格配对组：

- 规则基线；
- 规则 + 风险 sizing；
- LLM 在规则建议之上给出有限 override；
- LLM 无响应时的确定性 fallback；
- 可选的简单被动基准（现金/持有 SPY）。

策略对照要让输入、开始时刻、资金、最大总敞口、每标风险、费用/滑点和可交易标的完全相同。决策比较既看整体组合结果，也看 calibration、覆盖率、无交易/abstain 的质量、错误类别和风险调整后损失；不将单股 24h 探针合成组合收益。

**决策输出要求**：结构化字段包含 action/size 或 abstain、预测 horizon、概率或分位数、理由码、所依据的 snapshot id、约束余量及失效条件。Guardian 再独立裁决。缺行情、数据过期、输入越界或模型超时默认走安全 deterministic path。

**验收**：outcome attribution 覆盖率逐版本提升，先从现有 48% 无归因降至低于 5% 的新样本目标；冻结 holdout 上 LLM policy 对同约束 deterministic baseline 的净增量在预定义区间内为正，且风险、回撤、损失和交易成本不超预算。若置信区间跨过零或风险恶化，结论为“未证明”，保持原规则。

### D. RSI：让系统学习如何做研究

**目标**：除了找交易规则，还要从上一轮的实验结果改变下一轮的研究选择，并通过独立对照证明这种改变提高了研究效率或决策质量。

首个版本只允许调整**安全边界内的研究策略**：假设优先级、候选参数范围、数据集选择、需要的样本量、实验预算、已知失败模式和停止规则。不会修改 source、shell、Guardian 阈值、market-open 检查或执行权限。

每个 research episode 记录：

1. 本轮可用证据快照、上一轮 experiment IDs 和 outcome；
2. 系统选择的假设、为什么选择、预计区分力和预算；
3. 试验的固定 seed、候选数、计算耗时、数据成本、失败分类、verdict；
4. 下一轮计划相对上一轮的具体变化；
5. 外部 meta-evaluator 判断该变化是否减少无信息试验、缩短发现时间或提高通过候选在 holdout 的表现。

采用两条研究队列：冻结版本作为 control，RSI 版本作为 treatment；使用预注册相同数据、候选总预算和截止点。比较：有效候选数/CPU 小时、到达一个独立验证候选的耗时、过拟合淘汰比例、重复失败率、预先设定的 test performance 和风险。多轮之后都无增量，就认定 RSI 机制没有证据，保留固定研究基线。

**晋升边界**：通过离线 holdout 才能进入 shadow；shadow 有完整市场时点及模拟成交/延迟记录后，才可提出有期限、有限资金、限定策略数的 paper probation；probation 不通过自动退役并复盘。每次只放一个候选，避免 25 个待评候选挤占循环。硬风险上限不由模型调整；生产 journal、executor 和 broker credentials 不对研究代码开放。

**RSI 验收**：至少完成预先设定数量的 control/treatment episode（建议先 20 轮作为工程与效率评估，不称统计盈利证明），保留所有 episode；treatment 在预算匹配下改进至少一个预注册 meta-metric，且最终未触碰 alpha holdout；否则 RSI 判为未证明。交易 alpha 是否成立仍单独按 C/E 门槛判断。

### E. Shadow 与 paper 有限验证

**目标**：验证历史研究在实时信息、延迟、缺数据和实际 Guardian 下仍能正确行动。

阶段顺序：离线 holdout → shadow → 一个策略有限 paper probation → 与现役 deterministic baseline 配对比较。记录 signal timestamp、received timestamp、decision timestamp、order/fill timestamp；对行情陈旧、broker 部分成交、拒单、重启、滑点和现金约束建立测试。shadow 不送订单；paper 继续仅用 Alpaca paper endpoint。

**验收**：shadow 至少经历多种市场状态与预注册样本量，无 lookahead、数据泄漏、交易归属错乱、Guardian bypass 或安全事件；paper probation 达到事前限定的交易数/时间上限后自动给出 PASS、FAIL、INSUFFICIENT。任何风险控制失败立即停止该候选并保留证据；不能放宽限额来追求成交数或收益。

### F. 60 交易日的组合层证据

**目标**：修复 benchmark capital basis 后，比较滚动窗口中的整个 agent，而非把策略各自峰值资金相加当作同时使用资金。

完成后统一公布：同一时点组合资本/权益曲线、净成本、同期 SPY 与现金基准、净超额、最大回撤、波动/下行风险、日均及峰值 gross exposure、换手、成交/拒单/缺数、agent/owner 区分及模型归因覆盖。报告同时保留 daily paired return 序列，以便对窗口做置信区间和 block bootstrap。

**验收**：完整 60 个有效交易日且数据连续；流水和 broker reconcile 无未解差异；成本与滑点假设事前固定；目标为风险上限内扣成本后净超额的预设置信区间高于零，并且 drawdown/暴露均未越限。窗口不足、风险调整后不优、基准不同步或数据缺失都不得宣称达标。

60 日是最短观察窗口而非自动具有统计功效。若自相关、市场状态变化或样本不足使区间过宽，应延长观察或降级为“证据不足”，不得将 60 天硬当成统计保证。

### G. 基础设施效率和协作体验

**目标**：缩短“提出想法 → 得到可信结论”的周期，并能及时找出失败发生在哪一层。

- 统一 Makefile 命令入口：`install → fetch/validate → run research → evaluate → report`；明确实盘凭证永不属于此闭环。
- 静态测试/小单元测试、单一 dataset regression、完整 offline gate 分级运行；完整 research 不进入每次 CI。CI 跑逻辑门与 schema/fixture，夜间或人工运行高耗时真实研究。
- 对每个阶段输出 wall time、CPU、峰值 RSS、输入行数、候选数、试验数和失败原因；profile 证明热点后再优化。
- 先做 journal 多余扫描、重复解析和候选冗余的合并；以同一 fixture 对比前后耗时与指标。不要为组织代码而重写架构。
- 数据抓取采用增量、带 cursor 与断点续传；同一数据 hash 不重复付费/回测。研究与运行时依赖隔离、版本锁定并可从干净克隆安装。
- 报告和静态网站来自同一个 JSON snapshot，记录 commit/data hash/schema/as-of；私有运行数据与公共展示分离且默认脱敏。
- 运维以服务实际最后成功时间、健康周期延迟和错误率判断，不以 systemd timer active 或 unit active 代表研究成功。

**效率指标基线在第一个阶段测量，不先猜数字**：完整 research wall time/P95、CPU 小时/试验、单次 journal 扫描耗时、daemon 维护 P95、停止时间、失败重跑比例。设定改善目标后留原始命令和输出。

## 4. 每轮研究的标准操作清单

1. 先写评估记录：要解决的问题、当前实测 baseline、一个成功标准、验证命令、预注册停止条件。
2. 确认工作树/commit、实验数据 hash 和当前策略/模型版本；检查历史目录与未提交工作，不覆盖其他人的修改。
3. 先做数据及无泄漏 sanity check，再运行最便宜的测试；记录总候选数和预计预算。
4. 冻结假设、指标、窗口与成本后运行 search/walk-forward/多重检验；失败结果也入 ledger。
5. 只对满足门槛者运行 holdout 一次；否则结束该候选。出现问题时写出 root cause，然后开始下一轮。
6. 通过后依次走 shadow 和有限 probation；任何时候 Guardian、paper-only 与 market-open 不变量均不变。
7. 生成一份 report JSON 与可读摘要；对照 control，决定继续、停止或回滚研究方法。
8. 更新计划状态：仅将有命令输出支持的步骤标为完成；被阻塞或被替代的任务保留理由。

## 5. 优先执行顺序与拆分

每个包均单独 baseline → 方案 → 小改 → 检查 → 记录；不要把代码拆分/网站品牌与交易算法改变放在同一工作单元。

| 顺序 | 工作包 | 基线/目标 | 依赖与停手条件 |
|---|---|---|---|
| 0 | 固定评估合同和当期工作树 | 当前多份来源/未提交报告工具；产物为 experiment spec 与 performance schema | 先盘点未提交文件；不得丢弃或覆盖；合同未明确前不再增新 alpha 搜索 |
| 1 | 运行健康、SIGTERM 和异地备份 | 最近 error_count=1；停机 30 秒后 SIGKILL；journal 只在本机 | 只先做诊断与隔离恢复演练；恢复核对未通过时暂停依赖 journal 的研究结论 |
| 2 | 单一行情/研究 provenance 与缓存身份 | 最新运行成功，但之前 17 symbol timestamp 混用丢失 86% bars；现在按 SPY 隔离运行 | 复核 schema/source/timezone；任何跨标的或 PIT 错误都阻止后续试验 |
| 3 | Alpha search 规范和便宜筛选 | 134425 SPY bars、11 trials、0 PASS；当前候选有 100-trade 证据门槛 | 不降低门槛；先减少重复/无效候选并扩大可证伪的假设族；无通过则报告 null |
| 4 | 完整收益/风险/成本合同 | 1.81% vs SPY 2.69%；资本口径尚不够精确 | 固定 broker events；日报/HTML/CLI 共用计算；出现归因差异就不做策略晋升 |
| 5 | Decision policy 与 LLM 对照 | 52% 旧决策缺 model attribution | 从新决策起强制 trace；旧记录不补造；无足够配对样本不得宣称 LLM 有帮助 |
| 6 | RSI control/treatment | 当前仅有启发式演化流程，没有改进搜索方法的对照证据 | 在 alpha pipeline、评估合同稳定后开始；meta metric 失败则保留固定 search baseline |
| 7 | Holdout、shadow、paper probation | 60 个有效日与稳定 paper 对照尚未完成 | 仅允许经过冻结 holdout 的单个候选；hard limit 和 paper-only 不可调整 |
| 8 | 面板、公开站、开源 alpha 版本 | 六页已在来源代码，未完成真实浏览器/移动端/对外发布验收 | 使用脱敏统一 snapshot；不得将私有 journal/账户数据公开 |

时间估算在 package 0 之后由实际 benchmark 填入。本计划不把研究样本等待时间算成开发工期，也不承诺某个日期前一定发现 alpha。

## 6. 不可变的安全与研究约束

- 不接入 live order；完成 paper 行为审计与 Guardian 独立风险验收之前，不实现 live mode。
- Guardian 是唯一订单风险门；不得因为 queue backlog、样本不足、收益差或 deadline 放宽其限制。
- 所有 paper/research 数据均使用可追溯的真实行情、paper broker 证据或明确标注的合成测试；合成 fixture 不能支撑 alpha 结论。
- 不执行 LLM 产生的 shell、Python 或策略代码。新策略采用受 schema 限制的参数 DSL。
- 研究模块不能导入订单执行器、写 production journal 或读取 broker credentials；向生产候选提交经过可审计的准入界面。
- 训练/筛选/holdout 时序严格隔离；最终集一旦窥视，必须退休为开发数据。
- 每个失败的 search、timeout、数据不足和拒绝都留记录；不能只汇报成功候选。
- 不把过往正 paper PnL、策略数量增长或局部探针当成实盘盈利、安全性或 RSI 已达标证明。

## 7. 项目完成定义

### Alpha 发现可用

贡献者能从新克隆运行真实行情的固定研究任务，完整复现数据版本、全部搜索次数、成本和负结果；没有未来数据泄漏；候选在预注册的 OOS、统计、成本、容量及风险检查后明确归类为 PASS/FAIL/INSUFFICIENT。

### 决策能力可用

每个新决策有不可变 provenance 和后续结果；同一信息、资金、风险和成本条件下能比较 deterministic baseline、LLM augmentation 和 fallback；覆盖率、校准和组合净结果可计算；不确定、缺数、过期、超时能安全退让。

### RSI 可证实

每轮系统改进方法的变化及依据可追踪；冻结 control 对照能显示 treatment 提高预注册研究效率或决策 meta-metric；通过候选的 alpha 结果仍要独立验证。统计上不成立时，计划允许诚实得到“未证明 RSI 有增益”。

### Infra 可依赖

干净克隆、CI、Paper broker 路径、research job、report、backup/restore 均有可重复命令；真实服务健康由鲜活 cycle/heartbeat/last-success 衡量；坏数据、网络/LLM 超时、停止与恢复均可检测；报告数字来自一个可校验 snapshot。

全部完成前，项目定位维持：**带自我评估与受控研究能力的开源 paper-trading alpha**。方向 alpha、净超额、LLM 正贡献和 RSI 增益分别给出证据，不用其中一个冒充其余目标。
