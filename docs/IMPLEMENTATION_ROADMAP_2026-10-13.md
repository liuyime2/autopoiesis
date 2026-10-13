# Autopoiesis：现状、缺口与可执行路线

审查日期：2026-10-13（底特律时间）。这份文件是当前执行清单，不把计划、代码存在或 paper 账户权限当成运行验证。证据以可复现命令、提交、真实只读账户/模型观察和该 SHA 的 CI 为准。已有研究计划的完整方法细节见 [RSI 执行计划](RSI_EXECUTION_PLAN_2026-10-10.md)；本文件按当前依赖和缺口排序，更新后续执行状态。

## 当前结论

项目已经具备可审计的 paper 交易主路径、whole-account 只读清点、Guardian 风控、paper 下单/恢复基础设施、预注册研究工具和策略生命周期/反思/课程提案机制。最新源码修复仍在 PR 分支，当前运行的 daemon 尚未切换到它。项目尚无通过合同的 alpha，没有证明 LLM 决策净增益，没有证明受控递归自我改进 RSI 的收益，也没有完成统一资金口径下的长期 paper 组合超额验收。现在的状态是**准备做新源码 paper 验证**，不是已经验证能持续正确挣钱。

最新源代码 check 在本地实际通过 1,757 项、6 项跳过；完整的新提交树克隆及远端当前 SHA CI 尚待本轮重跑。账户权限、代码适配、当下持仓和成交验证是四种不同事实，不可互相替代。

## 按领域看已经有的证据和缺口

| 领域 | 当前实测/实现 | 仍不能声称完成 |
|---|---|---|
| 源码与测试 | 12 个有边界的单元修复通过独立复审；最新 `make check ENVFILE=/dev/null TEST_ARGS='-W error::pytest.PytestUnhandledThreadExceptionWarning'` 为 1,757 passed / 6 skipped，mypy 49 个源码文件无问题；unit-template 门禁覆盖 14 个模板，55 个 gate 类未删减 | 新鲜隔离克隆成功和最新 PR CI 仍需按最终 commit 重跑；测试不能证明生产账户或实际开盘行为 |
| 当前进程 | 只读观察到旧 paper daemon PID 2800047，源码指纹 `60d5aa8d767ce067`，market-closed maintenance；旧源码尚未加载本分支 | 新提交、新 heartbeat、启动协调和开盘后的实际决策仍未验证 |
| 账户 | 真实只读清点：6 个 us_equity 长仓 BIL、SPY、TLT、XLB、XLE、XLF，0 个完整读取的挂单；账户 ACTIVE，crypto status ACTIVE，options approved/trading level 3 | 这些许可不表示本项目有期权/加密交易适配。新持仓/余额/挂单需部署后重查 |
| 股票/ETF覆盖 | 股票/ETF 有行情、Guardian 和 paper 订单流水；whole-account `manage_account` 已启用。已冻结可选策略实际覆盖 SPY、BIL、TLT | XLB/XLE/XLF 目前安全地 HOLD；fractional 数量无法精确清零；short=shadow，整账户外部 short 回补未实现。不可称所有持仓均受策略管理 |
| 期权、加密及其他 | 未实现期权/crypto 专用订单适配、行情/生命周期/账务模型；未知 instrument 会拒绝走普通股票路径 | 不支持任意资产买卖。期权合约/乘数/指派/到期风险，crypto 24/7 时钟/精度/费用均需独立实现和验收 |
| 订单/风控 | 已有持久 intent、歧义提交阻断、对账/部分成交快照、整账户订单完整性、paper host 和 Guardian 检查；禁止自动降低硬限额 | 新部署后真实 startup reconciliation、停机期间恢复、替换链和首个实时开盘周期仍待验证。不因“希望成交”而放宽风险或强行下单 |
| 研究与 alpha | 实际拉取/校验 24,237 根真实 SPY 五分钟 bars；完整研究台账有 154 个试验，其中新增 11 个为 1 OVERFIT、10 INSUFFICIENT、0 PASS；重跑新增 0 个试验、回测 | 该窗口在注册研究合同之前已经开始，不是独立预注册/holdout。当前有效 alpha = 0。不能用模拟数据、候选数或策略数量宣布发现 alpha |
| 交易收益 | 历史 paper 日志曾记净诊断 PnL +811.72；历史报表资本分母按策略峰值敞口相加，不是同步组合权益，且成本/agent-owner 归属口径不满足目标 | 组合净收益率、同步 SPY/现金超额、置信区间和 drawdown 目标为 UNKNOWN/未通过；20 个记录日期也不是连续、完整 60 个有效交易日 |
| 决策与 RSI | 当前有反思、任务提案、策略准入和生命周期代码；修复后的停止检查禁止停止后新启课程阶段/模型请求 | 没有 control/treatment research episode。系统能提出并筛选任务不等于它已从试验结果学会如何改进研究，也不等于利润提升 |
| 基础设施 | Make 单一任务入口、服务模板、1200-cycle 配置、合作停止预算、真实 journal 本地恢复和 hourly backup timer 已存在；研究合同和运行状态采集工具已实现 | 最新代码部署、全运行态快照、定期恢复演练、异地备份目标/主机损失恢复、运行成功监控和新 timer 实际触发待确认。无异地存储目标，不要假称异地已完成 |
| 开源体验 | 去除 min-agent/min_agent 活跃代码引用的先前扫描为 0；pyproject 声明依赖；源码、Make、CI、fresh-clone 文档存在 | 本轮最终克隆/CI 尚未完成；完整贡献/发布门槛及真实网页无障碍/移动端/隐私验收仍待做。已有 HTML/页面代码不是可发布站点验收 |

本轮证据详情分别在[账户资产范围](evidence/account-asset-scope-2026-10-13.md)、[开盘验证计划](superpowers/plans/2026-10-13-market-open-validation.md)、[当前修复记录](evidence/rsi-repairs-2026-10-13.md)和[历史 RSI 审计](evidence/rsi-status-audit-2026-10-12.md)。历史数字只描述相应时点及其口径。

## 执行规约

每个代码工作包都按一条闭环执行：**测量问题与基线 → 写小计划和成功条件 → 最小修改 → Make 定向验证 → 独立规格/质量复审 → 必要的整库检查 → 更新证据和 Todo → 一个原因一致的提交**。新功能排队等待前一工作包的结果。Fresh clone/GitHub CI/实际进程都必须对应明确 SHA。

paper 路径只使用真实数据；测试夹具必须明确标成 synthetic。继续禁止 live 交易、伪造研究、模型 shell 执行、跳过市场开市检查、跳过 Guardian、自动松绑硬风险。成交、盈利、RSI 和资产覆盖只有出现相应真实证据后才能标绿。

## 当前优先执行项

### 0. 结束本轮 source 与发布核验

**起点**：本路线文件写入时，source 已提交到 `codex/rsi-audit-repairs`；本地整套 check 为绿，单位 12 两轮复审已批准；更早 c2fa05d 独立克隆为 8/9，明确失败在 unit template 门禁。新模板门禁修复已审查，但包含最新源码和所有 14 个模板的新克隆尚未运行。

**执行**：

1. 运行 `make check ENVFILE=/dev/null TEST_ARGS='-W error::pytest.PytestUnhandledThreadExceptionWarning'`，若源代码/测试后续有任何更改必须重跑。
2. 通过 Make 入口从最终已提交 tree 做新 Python 环境克隆：`make fresh-clone ENVFILE=/dev/null PYTHON=/localscratch/liuyime2/conda_env/llm/bin/python`。要求每个步骤成功、gate 55/55 类，保存原始实际成功日志；若失败，保留失败证据并先写测量计划修复。
3. 将成功克隆日志按原样提交，再 push 现有 PR #10。检查**PR 当前头 SHA**的四个 CI job（Python 3.10/3.11/3.12 gate 与 release）全部 success；旧 SHA CI 不能替代。
4. 最终 combined review 确认没有未解决 source blocker。PR 保持 open/unmerged，直到授权合并并且合并基线另行复验。

**通过条件**：本地全套、credentials-free fresh clone、指定 SHA 四个 CI job、完整 PR diff/review 全部通过；日志 commit under test 等于实际 clone SHA。不通过时 NO-GO，不部署。

### 1. 纸交易源码切换和本次开盘观测

**执行前**：核对 broker clock/market calendar、当前 process PID/cwd/fingerprint、heartbeat 和交易服务状态；确认老进程处于 market-closed maintenance sleep，backup/研究/report/watchdog 不会同时写共享状态；检查真实 systemd user unitdir `/home/liuyime2/.config/systemd/user`。operator env 只从外部配置读取，不在 shell 输出中暴露凭证。

**执行**：

1. 保存每个原 unit 的精确字节、timeout 和启停状态；已有用户 prereg.py 修改的 SHA 必须与审查起点一致。
2. 暂停 watchdog、research、report、backup 等可能竞争写入的 timer/service；确认当前备份任务完成。保留现有 2,326-cycle journal snapshot 证明。协作停止旧 daemon；其旧 wait 可达 675 秒，临时 stop grace 705 秒仅供这一次安全停止。禁止 SIGKILL 穿过 journal/order 写入。退出后立即去掉临时 override，再确认新预算 630 秒。
3. 在旧进程退出后，先复制完整停止时点 journal 和可变策略/知识/反思/研究状态至私有 checkpoint，逐项核验清单与 SHA。备份应覆盖状态而非凭证；旧 journal 不覆盖、不重置。把修复 worktree `runtime/` 与主运行态合并为唯一 shared runtime，验证 journal、lock、library/cache 路径指向同一位置。
4. 用已 CI 通过的 worktree 调用现有 `make install-service ENVFILE=<外部 env>` 渲染服务；逐个实际路径用 `systemd-analyze --user verify` 验证。风险配置保留 paper、MAX_DAILY_CYCLES=1200、MAX_POSITION_VALUE=25000、MAX_TOTAL_EXPOSURE=90000、MAX_TRADES_PER_DAY=100、SHADOW=0、SHORTS=shadow 和现有 symbol/allowlist。核对启动主机是 Alpaca paper。
5. `make run ENVFILE=<外部 env>` 启动单一 daemon。核对 PID/cwd、工作树 fingerprint 与 heartbeat 等值；检查旧进程完全退出、startup reconciliation 通过、账户和订单同步完整、journal 可追加。停用/恢复 timer 的每次变更都检查 manager 实际 active state，而非只看 enable 成功。
6. 按计划安装 08:45 preflight、09:45 与 10:30 open verifier。`make market-preflight` 要求真实行情、一个覆盖标的真实模型决策、Guardian 在市场关闭/行情拒绝时安全拒绝、零新订单；`make market-open-verify` 在开市前的实际结论只能 WAIT，不能硬造 open PASS。
7. 09:45/10:30 读取只读报告和真实 paper journal：检查新 session cycles、每条 strategy.symbol 覆盖 snapshot.symbol、冻结 provenance、Guardian、提交 intent/ack ID、拒单/成交/成本、误差、fallback、未完成 fills。覆盖不足/无成交可以是 HOLD/WAIT；只能按报告契约解释，不能算盈利或整体交易成功。

**通过条件**：当前进程运行新指纹；风险配置逐项一致；唯一 writer 和本地可恢复 checkpoint；服务与 timers 真正 active；pre-open read-only PASS；open-session observation truthful。任何项失败都保持纸交易/停止新增交易，保存证据、定位 root cause、不要自动回退到已知 provenance 有缺陷的旧代码。新源码已准备不表示本次开盘必然有可交易机会或有利润。

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
