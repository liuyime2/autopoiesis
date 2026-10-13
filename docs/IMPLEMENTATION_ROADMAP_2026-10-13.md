# Autopoiesis：现状、缺口与可执行路线

审查日期：2026-10-13（底特律时间）。这份文件是当前执行清单，不把计划、代码存在或 paper 账户权限当成运行验证。证据以可复现命令、提交、真实只读账户/模型观察和该 SHA 的 CI 为准。已有研究计划的完整方法细节见 [RSI 执行计划](RSI_EXECUTION_PLAN_2026-10-10.md)；本文件按当前依赖和缺口排序，更新后续执行状态。

## 当前结论

项目已经具备可审计的 paper 交易主路径、whole-account 只读清点、Guardian 风控、paper 下单/恢复基础设施、预注册研究工具和策略生命周期/反思/课程提案机制。**截至 2026-10-13 06:16 EDT**，修复源码已部署到 paper daemon，fingerprint `9fb9066d820f27c9`；本地全套检查、隔离 fresh clone、提交 `da2da73` 的四项 GitHub CI 都通过。真实账户只读 smoke 和闭市安全探针通过，开盘定时器已安装，但当日正式开盘观察还没发生。

项目仍没有通过研究合同的 alpha，没有证明 LLM 决策净增益，没有证明受控递归自我改进 RSI 的收益，也没有完成统一资金口径下的长期 paper 组合超额验收。当前状态是**部署已完成、等待开盘 paper 观察**，不是已经验证能持续正确挣钱。账户权限、代码适配、持仓覆盖、实际成交和盈利是不同事实，不可互相替代。

## 按领域看已经有的证据和缺口

| 领域 | 当前实测/实现 | 仍不能声称完成 |
|---|---|---|
| 源码与测试 | 12 个有边界的单元修复通过独立复审；`make check` 为 1,757 passed / 6 skipped，mypy 49 个源码文件无问题；fresh clone 9/9，unit-template 门禁覆盖 14 个模板；提交 `da2da73` 的 release 与 Python 3.10/3.11/3.12 四项 CI 成功 | PR #10 仍 open/unmerged；测试不能证明生产账户或实际开盘行为 |
| 当前进程 | 新 paper daemon PID 3184440，fingerprint `9fb9066d820f27c9`，active/running、零重启；startup reconciliation matched；watchdog 在 06:16:38 EDT 自动触发成功 | 当日市场尚未开盘；08:45/09:45/10:30 的真实开盘时段观察仍待发生 |
| 账户 | 06:08 EDT 真实只读清点：6 个 us_equity 长仓 BIL、SPY、TLT、XLB、XLE、XLF，0 个完整读取的挂单；账户 ACTIVE，crypto status ACTIVE，options approved/trading level 3 | 这些许可不表示本项目有期权/加密交易适配。后续需重复核对新持仓、余额及挂单 |
| 股票/ETF覆盖 | 股票/ETF 有行情、Guardian 和 paper 订单流水；whole-account `manage_account` 已启用。已冻结可选策略实际覆盖 SPY、BIL、TLT | XLB/XLE/XLF 目前安全地 HOLD；fractional 数量无法精确清零；short=shadow，整账户外部 short 回补未实现。不可称所有持仓均受策略管理 |
| 期权、加密及其他 | 未实现期权/crypto 专用订单适配、行情/生命周期/账务模型；未知 instrument 会拒绝走普通股票路径 | 不支持任意资产买卖。期权合约/乘数/指派/到期风险，crypto 24/7 时钟/精度/费用均需独立实现和验收 |
| 订单/风控 | 已有持久 intent、歧义提交阻断、对账/部分成交快照、整账户订单完整性、paper host 和 Guardian 检查；新部署 startup reconciliation matched；禁止自动降低硬限额 | 停机期间恢复、替换链与首个正式开盘周期仍待验证。不因“希望成交”而放宽风险或强行下单 |
| 研究与 alpha | 实际拉取/校验 24,237 根真实 SPY 五分钟 bars；完整研究台账有 154 个试验，其中新增 11 个为 1 OVERFIT、10 INSUFFICIENT、0 PASS；重跑新增 0 个试验、回测 | 该窗口在注册研究合同之前已经开始，不是独立预注册/holdout。当前有效 alpha = 0。不能用模拟数据、候选数或策略数量宣布发现 alpha |
| 交易收益 | 历史 paper 日志曾记净诊断 PnL +811.72；历史报表资本分母按策略峰值敞口相加，不是同步组合权益，且成本/agent-owner 归属口径不满足目标 | 组合净收益率、同步 SPY/现金超额、置信区间和 drawdown 目标为 UNKNOWN/未通过；20 个记录日期也不是连续、完整 60 个有效交易日 |
| 决策与 RSI | 当前有反思、任务提案、策略准入和生命周期代码；修复后的停止检查禁止停止后新启课程阶段/模型请求 | 没有 control/treatment research episode。系统能提出并筛选任务不等于它已从试验结果学会如何改进研究，也不等于利润提升 |
| 基础设施 | Make 单一任务入口、服务模板、1200-cycle 配置、合作停止预算；部署前 certified journal 与 204 个运行态文件 SHA 核验；本地 backup timer 成功；watchdog 自动触发成功 | 完整异路径恢复演练、异地备份目标/主机损失恢复、60 日监控和新 readiness timer 实际触发待确认。无异地存储目标，不要假称异地已完成 |
| 开源体验 | 去除 min-agent/min_agent 活跃代码引用的先前扫描为 0；pyproject 声明依赖；源码、Make、CI、fresh-clone 文档存在；当前 source SHA 的 clone 与 CI 证据通过 | 完整贡献/发布门槛及真实网页无障碍/移动端/隐私验收仍待做。已有 HTML/页面代码不是可发布站点验收 |

本轮证据详情分别在[账户资产范围](evidence/account-asset-scope-2026-10-13.md)、[开盘验证计划](superpowers/plans/2026-10-13-market-open-validation.md)、[当前修复记录](evidence/rsi-repairs-2026-10-13.md)和[历史 RSI 审计](evidence/rsi-status-audit-2026-10-12.md)。历史数字只描述相应时点及其口径。

## 执行规约

每个代码工作包都按一条闭环执行：**测量问题与基线 → 写小计划和成功条件 → 最小修改 → Make 定向验证 → 独立规格/质量复审 → 必要的整库检查 → 更新证据和 Todo → 一个原因一致的提交**。新功能排队等待前一工作包的结果。Fresh clone/GitHub CI/实际进程都必须对应明确 SHA。

paper 路径只使用真实数据；测试夹具必须明确标成 synthetic。继续禁止 live 交易、伪造研究、模型 shell 执行、跳过市场开市检查、跳过 Guardian、自动松绑硬风险。成交、盈利、RSI 和资产覆盖只有出现相应真实证据后才能标绿。

## 当前执行队列（按依赖顺序）

### 0. 已完成：source 与发布核验

**起点**：本路线文件写入时，source 已提交到 `codex/rsi-audit-repairs`；本地整套 check 为绿，单位 12 两轮复审已批准；更早 c2fa05d 独立克隆为 8/9，明确失败在 unit template 门禁。新模板门禁修复已审查，但包含最新源码和所有 14 个模板的新克隆尚未运行。

以下是已完成结果（不是需要重复执行的步骤）：

1. `make check` 对本轮 source 通过 1,757 项、6 项跳过；mypy 检查 49 个源码文件、零问题。
2. `make fresh-clone` 在隔离 Python 3.10.18 环境通过 9/9；55/55 gate classes、2,900 次执行、94 个测试文件；凭证和 XDG 配置均移除。
3. 原始克隆证据已提交；PR source commit `da2da73` 的四个 GitHub Actions job（Python 3.10/3.11/3.12 与 release）均成功。
4. 最终独立复审没有 source blocker；PR #10 仍 open/unmerged。合并授权与合并基线验证仍是单独关口。

**验收结果**：本地全套、credentials-free fresh clone、提交 `da2da73` 四个 CI job 均通过；原始 fresh-clone 日志记录实际测试 tree `dbd55c2`，随后日志按原样提交。PR #10 保持 open/unmerged。此次源码/克隆/CI 关口 PASS；PR 最终合并后的基线仍需重新验证。

### 1. 已完成部署；待完成当日真实开盘 paper 观测

**执行前**：核对 broker clock/market calendar、当前 process PID/cwd/fingerprint、heartbeat 和交易服务状态；确认老进程处于 market-closed maintenance sleep，backup/研究/report/watchdog 不会同时写共享状态；检查真实 systemd user unitdir `/home/liuyime2/.config/systemd/user`。operator env 只从外部配置读取，不在 shell 输出中暴露凭证。

部署步骤已完成并记录在 [实际部署证据](evidence/market-open-readiness-2026-10-13.md)。不要重复停止/安装服务。剩余执行项：

1. 当前已启用 08:45 preflight、09:45 与 10:30 open verifier timers；逐个读取结果，不把 active/enabled 当执行通过。
2. 在每个到期点之后，读取只读报告和真实 paper journal：核验新 session cycles、每条 strategy.symbol 覆盖 snapshot.symbol、冻结 provenance、Guardian、提交 intent/ack ID、拒单/成交/成本、错误、fallback、未完成 fills。
3. 若结果为 NO-GO/WAIT，按诊断修复具体根因后重复同一检查；保持单一 daemon，不并行启动第二个 trader。任何新增订单都必须经过现有 Guardian、市场时间与 paper broker 路径。
4. 在后续每个交易日重复账户/订单对账与完整性监控，开始新的 60 个有效交易日序列；缺失或不一致的日子按预先定义的规则标记，不补造、不推断。

**通过条件**：pre-open/readiness timers 的真实报告符合契约；至少一条完整 open-session cycle 有真实新数据、source/provenance、Guardian 与 broker 对账证据；没有未解释订单或 journal 不一致。无交易机会可安全 HOLD，但这只证明安全运行，不证明 alpha/盈利。任何项失败都保持 paper 模式、停止新增交易并保留证据；不回退到已知 provenance 有缺陷的旧代码。

### 2. 账户完整性与逐资产可控范围

依赖第 1 项稳定且账户/订单是同一时点完整读取。将 scope 的“任意资产可买卖”转化成**每种资产独立资格**：`broker instrument metadata → market data → position/quantity model → strategy coverage → risk calculation → order policy → lifecycle/accounting → reconcile → offline tests → paper/shadow evidence`。每一层都缺一不可。

先处理当前股票/ETF：真实全账户快照定时重复；按持仓及 attention symbol 测 coverage。XLB/XLE/XLF 如无冻结策略，行为就是 abstain/HOLD，需研究和独立评估后才能覆盖。给 fractional quantity 定义明确拒绝/准确清算流程；实现覆盖账户外 source positions 的 short cover 前保持 shorts=shadow，禁止超卖。核对 market/limit/extended-hours 订单、撤单/替换链及费用现有支持边界。

期权、crypto 分别写自己的测量与规格计划，不扩充现有股票 gateway 来猜测处理：期权需链/合约符号、multiplier、报价时效、spread/liquidity、组合最大损失、到期/行权/assignment、公司行动/费用和 broker reconciliation；crypto 需交易对 metadata、24/7 session/clock、精度/最小下单额、费用、订单类型、碎额持仓和结算账本。先只读和拒绝测试，然后 shadow，之后才允许基于 paper 证据做有限评估。每类分别验收，不共用未定义的风险假设。

**通过条件**：支持清单与故障矩阵按真实接口列出；账户全量持仓、现金、购买力及挂单可完整核对；未知/不支持资产不能进入下单；每种已支持资产从数据到成交反馈的 paper 场景独立验证。权限等级永远不等于适配器已完成。

### 3. 统一收益账本与“知道如何决策”的验证

先冻结一个 agent 资金合同：初始 allocation 和 owner cash flow 的时间点、同步组合 equity、持仓/现金/购买力、未实现/已实现 PnL、手续费/滑点/借券费用、外部 owner 交易隔离、基准对齐规则、风险及最大回撤。历史无法重建的金额写 UNKNOWN；不得拿策略峰值资金之和作资金分母。

只从本合同冻结后的决策积累数据：snapshot/quote 时间、instrument、market regime、策略/数据/evaluator/模型/prompt hash、输入版本、action/size/abstain、预测 horizon 与 confidence、Guardian 结果、intent/broker order/fill/cost、统一 outcome horizon。Outcome 等待期内保持 pending，不删失败、拒绝或未成交记录。

在相同标的、时间、资金/敞口/风险和成本下配对 deterministic policy、deterministic+LLM override、纯 fallback 和适当的被动基准。用 walk-forward 做开发筛选，封存 untouched holdout 单次验收。比较净 paired return、calibration、置信区间、drawdown、downside risk、abstain、覆盖率和风险拒绝；用 block bootstrap 或适合时间序列的推断控制相关性。禁止从未同场、不同资金池的策略 peak return 推导 LLM 增益。

**通过条件**：新决策 provenance 完整、配对 outcome 覆盖率达预定义目标、全部成本可核对，冻结 holdout 上相同约束的净增量置信区间越过预注册正阈值且 drawdown/exposure 不恶化越限。证据不足就保持 LLM 增益 UNKNOWN。

### 4. 可信 alpha 搜索及候选晋升

先选有 PIT/可追溯来源、时区/交易日历明确的 canonical 数据集，内容级 fingerprint 覆盖排序后的时间戳、标的、价格/必要特征；验证缺失/重复/非顺序/异常/幸存者偏差风险。分开开发、walk-forward、封存 holdout，按交易成本/滑点/报价约束现实化。研究 spec 在接触结果**之前**冻结 symbol、bar interval、窗口、规则族、参数候选、预算、最小交易数、统计检验和停止规则。验证实际生成的搜索空间确实服从声明规则，完整登记每次失败。

减少候选重复要按数据 hash、合同 hash 和 evaluator identity；搜索报表提供候选数、试验数、重复数、verdict 分布、耗时、CPU/内存、数据窗及版本。多重检验和候选选择偏差必须在独立验证控制，禁止反复查看 holdout 并调参。

**晋升顺序**：预注册的 cheap screen → walk-forward → untouched holdout → 逐项人工/治理审查 → shadow → 单个、限时 paper probation。一次只晋升一个策略；fail/insufficient 保留原样，禁止通过增加候选数或降低阈值追逐 PASS。新窗口的 11 个不通过试验保持在历史 ledger，不重置。

**通过条件**：最少样本数、成本后 edge、统计不确定性、稳定性/风险阈值、独立重跑 hash 全部符合预先冻结合同。当前 alpha 搜索实测 0 PASS；不能从已有研究结论直接跳到候选执行。

### 5. 递归自我改进 RSI 控制实验

RSI 只改研究方法和下一轮实验优先级，不给系统改 Guardian、执行权限、市场时间检查、仓位限额、source/shell 或 paper/live 开关的能力。若未来提议改策略/提示，必须作为版本化候选通过外部评估，人工审核后再进入下一阶段，研究 agent 无权自行发布。

冻结一套 research control 和 treatment：同版本、同 real data snapshot、同候选总预算、同 CPU/时限、同训练/评估/holdout 隔离。Control 使用固定策略选假设；treatment 可读取上轮失败 lesson/效率结果，提出明确的下一轮策略变化和可证伪原因。所有 episode 写 experiment IDs、证据、差异、耗时、重复率、失败类型、预算、模型/source/data hash、下一轮改变及 evaluator verdict。meta-evaluator 不能是 treatment 自评。

先以 20 个成对 episode 评估工程/效率 meta-metric（例如达到合格候选所用预算、无信息重复率），这不是统计盈利证据；再按事前功效分析决定独立更多轮次。RSI 只能在不损失 alpha holdout 隔离和风险边界时通过。没有有效 control/treatment 不称 RSI 已开始；提案/lesson 数量不算增益。

**通过条件**：预注册 meta metric treatment 增益区间达到阈值，同时数据质量、候选 alpha、风险及 holdout 均不回退；否则结论“不支持 RSI 有改进”，保留 control。

### 6. Paper 时间序列、可靠备份和恢复

连续 60 个有效交易日计时从已部署新代码、风险/账户/进程/完整记录对齐后重新开始或标为历史窗口；提前定义暂停日和数据缺失规则，不能把 60 个任意 log 日期当作达标。每天对 broker book、现金/权益、未结订单、journal、fills 和账户外 activity 做 reconcile；输出同步 SPY/现金基准及净成本收益/回撤/暴露的 paired series。缺数据、不一致、超限立即进入 NO-GO 并冻结晋升。

先完成旧 writer 停止后的**完整本地 snapshot**，不仅是 journal：策略、knowledge、reflection、curriculum、preregistration、risk baseline、registry、signal/research ledger 和配置路径清单；记录 SHA、容量、权限、包含/未包含项并做异路径恢复和语义 diff。编排每日 timer、成功时间、失败告警和重试；至少每月恢复演练。拿到实际用户提供的异地目标后，才实现加密异地副本、保留政策和主机损失恢复演练；在目标不存在时只报告本地可恢复。

效率按真实基线决定：daemon maintenance P50/P95、每次 journal 扫描 wall time/RSS、broker API P50/P95/错误率、完整 research wall time/CPU/候选、重试比例、deploy stop/start 耗时、备份大小/持续时间。定位热点并做一个优化，再用同一输入回归。先避免重复全量读取和无效试验，禁止没有测量依据的大重写。

### 7. 开源标准、网页与发布

在 CI/clone 达标后维护 CONTRIBUTING、行为守则、支持资产声明、凭证/隐私说明、风险设计、数据来源与可复现快速入门、paper-only 默认、版本兼容范围、安全漏洞反馈、发布/回滚记录。审核依赖许可证、固定/约束依赖、GitHub Actions permission 最小化、CI required checks、secret scanning 和可复现 release artifact。发布说明要包含真实 evidence 边界，不宣称盈利/RSI 成功。

网页只读取脱敏、签名/带版本、可复现的静态 report JSON。不得暴露账户余额、token、journal 原文、交易理由中的私人数据。以匿名 sample fixture 和真实 synthetic label 做桌面/移动浏览器、键盘可达性、色彩、错误/空/旧数据、构建/部署和公开 URL 验收。将网页品牌/改名与交易/风控修改分开提交；先完成项目名仓库引用/包名/CLI/域名/许可证兼容清单，审查迁移兼容再改名，不在当前纸交易验证中引入重命名破坏。

## 不能打勾的目标

以下项目即使相邻代码已存在，也保持未完成，直到对应真实验收出现：完整持仓自动化覆盖；期权/crypto/任意资产实盘或 paper 适配；独立有效 alpha；LLM 相对等约束基线增益；RSI control/treatment 改进；自动赚钱；同步净超额与风险合格的完整 60 日窗口；完整异地恢复；实际网站对外体验验收。任何一项都不能由单元测试、页面、systemd active、broker 权限、单次 PASS、历史合计余额或一笔成交替代。

## 本次执行增量（2026-10-13 05:40 EDT）

最新冻结源码在 05:17 EDT 的 `make check ENVFILE=/dev/null TEST_ARGS='-W error::pytest.PytestUnhandledThreadExceptionWarning'` 通过 **1,757 / 6 skipped in 47.53s**。该树上之后的文档提交只增加了本路线文件和开盘计划记录。独立 Python 3.10.18 credentials-free clone 对 `dbd55c2` 于 05:40 EDT 全部 **9/9 步骤 PASS**；`make check` 和单独 `pytest` 各 **1,757 passed / 6 skipped**；集成门禁 **55/55 classes、2,900 executions / 94 files**；包括 self-test 与 14/14 unit templates。原始成功输出在 `docs/evidence/fresh-clone.log`，已确认凭证和子 XDG 配置清空。日志变更随后独立提交；精确提交及 GitHub CI 状态按下文增量更新。

05:38 EDT 对旧 daemon 只读确认：PID 2800047 仍是 active/running，heartbeat RUNNING、broker 最近读取成功、market-closed maintenance、cycle/error 0/0；其代码指纹仍为旧 `60d5aa8d767ce067`。旧 30 秒停机窗口和旧代码未被动过。本轮可说“本地源码检查 + 新克隆通过”，不可说“生产 daemon 已更新”或“开盘已验证”。

GitHub 对完整提交 `da2da73bf9b3812aa2cd1e1b87cd67faff783c7f` 启动 run **38378167116**：release 与 Python **3.10 / 3.11 / 3.12** 四个 job 均为 success。三个版本各自的 install、lint、type、tests、offline smoke、self-test 和 integration gate 完成。该 commit 的 fresh-clone 日志记录实际 clone SHA 为 `dbd55c2`；`da2da73` 只增加原始日志和路线状态文字，代码/测试树没有变化。PR #10 仍 open、未合并。旧 daemon 尚未更新，因此生产部署与当天市场会话验证仍是待执行关口。


## 实际部署增量（2026-10-13 06:08 EDT）

见 [本次盘前/部署原始证据](evidence/market-open-readiness-2026-10-13.md)。新 paper daemon PID 3184440 正常运行，源码 fingerprint `9fb9066d820f27c9`；实际风险值与账户管理配置保持原值；startup reconciliation 为 matched。旧 daemon 合作停止退出码 0，未 SIGKILL；原 journal 及 204 个应用状态文件本地核验完成；新本地 journal backup timer 06:01 成功。本次没有外部异地副本。

真实 `make smoke` 06:08 EDT exit 0：账户 ACTIVE、6 个 us_equity 持仓、完整挂单 reader 共 0 个挂单；crypto ACTIVE 与 options level 3 只是券商账户许可。早间真实盘前 readiness 06:02 EDT 为 READY：模型有效响应与 SPY provenance 存在，market closed / scheduler false，决策 HOLD 0，Guardian 以 STALE_DATA 拒绝，无订单；报告仍标 profitability UNPROVEN、其他 attention coverage UNOBSERVED。闭市 open verifier 报 WAIT / SESSION_NOT_OPEN，scheduled Make wrapper exit 0。纽约时间 08:45 preflight、09:45 和 10:30 open verifier 已实际装为 active timers，未来观察尚未发生。

新源码、全新克隆、exact SHA CI均有证据；最终执行计划仅留下实际停机时长未计时及上述未来 session evidence 两项。没有新 session cycle/fill 或真实盈利结果，也没有证明 RSI。
