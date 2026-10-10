# 完成度、开源就绪与剩余工作审查

审查时间：2026-10-10，远端状态最后核对于 15:57 UTC。被审查提交：`bfad071eae66223c7179fc93f02ab8483a41feb6`。本报告是审查结果与实施计划，不是通过验收的声明。

## 1. 评价目标与边界

**问题**：确认每个主要部分是否可按设计使用、旧项目是否退役、GitHub CI 是否通过，以及下一步到底应修什么。

**基线**：审查开始工作树干净；45 个包内 Python 文件、78 个测试文件；GitHub 默认分支 main 与本地 HEAD 同提交；实际记录 19 个交易日，目标 60 个；页面生成器输出四页。

**成功标准**：区分实现、实际运行、测试、研究有效性、发布就绪；每个重要判断给出命令或源代码位置；形成带验收条件的待办，不改交易代码、不下单、不重启服务。

**验证方法**：检查当前入口、配置、风控、账本、研究、报告、面板、服务、CI 和社区文件；通过 Makefile 实际运行检查；隔离克隆重新安装；对风险配置使用明确标注的离线合成测试夹具，对成本使用现有真实 paper 账本计算。合成测试不作为真实交易表现证据。

审查期间另一个工作单元修改了 `tools/verify.py`，并运行会临时创建故障 canary 的 self-test；收尾检查时 `docs/history/` 已出现外部暂存删除。保留这些工作，不将其算成本轮审查的修改，也不将其算成已验收发布。历史文件数量和旧名统计针对上述提交及审查开始状态。本轮完整 `make verify` 因共享工作树发生变动而主动终止，退出 143；不能作为当前提交完整 gate 通过或失败的证据。self-test 与普通测试必须顺序执行或位于独立 checkout。本轮没有修代码，也没有提交或推送。

## 2. 总体判断

项目已经是有真实 paper 运行记录、策略生命周期、确定性风控和账本的研究型 alpha。已经具有开源许可证与社区入口，但尚未通过可复现发布验收。不能确认所有部分都可按设计使用，更不能确认持续盈利、自演化提升收益或 LLM 增益已完成。

不报告一个虚构的“项目完成百分比”。可测量的是：目标观察样本覆盖 **19/60 = 31.7%**；六类规划面板中四类已有初版，四类本身也未完整验收。这两项都不是整个项目完成率。

## 3. 实测结果

| 验证 | 结果 | 能证明什么 / 局限 |
|---|---|---|
| 本机 `make check` | 退出 0；Ruff 通过；mypy 45 文件无问题；1151 passed，272.05 秒 | 现有环境中这套断言通过；不证明新版依赖或整个实际部署可用 |
| 本机 `make smoke-offline` | 退出 0；导入正常；skip-broker doctor ok=True，9 warnings | 两条检查使用 `|| true`，仍可能吞掉非预期崩溃 |
| 隔离克隆 `make install` | 退出 0 | 已测 Python 3.10 新 venv；不是另一台机器的验收 |
| 隔离克隆 `make check` | lint 失败，make 非零 | 新安装 Ruff 0.17.0 报 RUF071；完整 fresh-clone 路径未通过 |
| 隔离克隆 `make type` | 退出 0，45 文件无问题 | 只证明类型检查 |
| 隔离克隆无凭证 `make smoke-offline` | target 退出 0；doctor 自身因缺配置退出 1 | 预期缺配置与真正程序崩溃还未严格分开 |
| `make benchmark` | verdict NOT MET；工具退出 1，make 退出 2 | 当前目标未达成；另发现 agent 成本口径错误 |
| `make integrity` | 退出 0；2060 cycles、20502 events、77 strategies | 当前台账检查通过，6 项已知历史缺失获明确豁免；不证明永久可恢复 |
| `make status-page` | 退出 0；四个 HTML 生成 | 文件生成成功；未做浏览器、无障碍或移动端验收 |
| `make daily-report DATE=2026-10-09` | 失败，make 退出 2 | `_headline()` 参数缺失；与定时服务故障一致 |
| 本轮完整 `make verify` | 主动终止，退出 143 | 并发修改及 canary 使此轮不适合发布验收；没有完整 PASS 结论 |

隔离克隆路径：`/tmp/autopoiesis-review-20261010.XXqqGV`，有效环境 `.venv310`。本机系统 Python 3.12 缺 ensurepip，按文档所述需 python3-venv；此后使用已安装 Python 3.10 创建独立环境完成安装。现有 `docs/evidence/fresh-clone.log` 针对旧提交 `1e2c679`，其中 Ruff 为 0.16.10，不能拿它证明当前 HEAD 与最新安装依赖通过。

## 4. GitHub CI：明确没有通过

最新运行：[38063755931](https://github.com/liuyime2/autopoiesis/actions/runs/38063755931)，被测提交为 `bfad071`，状态 completed / failure。

Python 3.10、3.11、3.12 三个 job 全部安装成功、lint 失败；type、pytest、offline smoke、self-test 和完整 gate 全部 skipped。因此不能称为“三版本测试通过”。公共日志下载返回 403；没有读取到远端具体 Ruff 报错文本。

独立克隆能够复现对应阶段的失败：`pyproject.toml` 的 `ruff>=0.6` 安装了 0.17.0，`tools/benchmark.py:158` 的 `os.path.commonprefix` 触发 RUF071。这里比较的是 doctor 描述文本，不是路径；直接套用规则建议的 commonpath 会改变含义。应选择明确的字符串前缀实现或有解释的局部规则例外，并验证受支持工具版本；不能全局关闭 lint 来变绿。

本地 master 显示 ahead 25，是相对旧 origin/master；GitHub main 已与本地同 SHA，不能据此说有 25 个提交未推送。

## 5. 旧名称与旧系统是否完整删除

| 范围 | 审查结果 |
|---|---|
| src/tools/tests/configs/Makefile/minictrl/CI/metadata/当前主要文档 | 审查时旧 `min-agent` / `min_agent` / 大写配置前缀引用为 0 |
| Python 包、CLI、配置读取 | 正式入口为 autopoiesis；未发现旧模块可导入、旧命令或旧配置 fallback |
| 旧 daemon | `min-agent.service` not-found、inactive/dead；新 autopoiesis.service active/running |
| 已部署 research unit | 仍有 `MIN_AGENT_MODE=paper` 与旧 min-agent runbook Documentation 路径 |
| 跟踪历史/原始证据 | 15 个文件仍有旧名，包括迁移档案、旧计划和 fresh-clone 原始日志 |

结论：**运行代码命名迁移基本完成，旧主服务退役；全机器与全部文本“完全删除”不成立。** 先清理失效部署引用、重新安装 unit、验证参数和运行指纹。历史与原始证据应保留语境或明确归档，不改写日志或 Git 历史制造“从未存在”的印象。本报告也会明确提到旧名；文本出现旧名与旧执行路径仍生效是不同问题。

## 6. 按功能逐项验收

| 部分 | 状态 | 当前边界 / 未完成项 |
|---|---|---|
| 包安装、CLI、Makefile | 条件可用 | 原有环境可运行；新依赖安装后的完整检查失败 |
| broker 数据与 paper 执行 | 已有真实使用证据 | 主服务运行；本轮未发送订单或额外触发交易周期 |
| 市场开放、行情新鲜度、paper-only | 有实现与回归覆盖 | 最新行情时间修复已提交；异常 broker 场景仍需独立端到端故障验收 |
| Guardian 硬限额 | 未通过完整安全验收 | NaN 配置能使对应限额比较失效，见第 7 节 |
| 规则、LLM 与 fallback | 可运行，效果未证实 | 模型归因缺失约 52%；局部探针不能代替组合收益证明 |
| FIFO 与成交归属 | 账本检查通过 | agent 汇总成本另用错误公式；所有报表口径仍未统一 |
| 评分、反思、记忆、curriculum | 有实现与运行记录 | 生命周期运行不等于改进收益，需服务/成交/样本外证据 |
| admission、probation、active、pause、retire | 条件可用 | 台账有 81 admitted、181 rejected、17 retired；面板错误描述 PAUSED 为终态 |
| short/cover 与 shadow | 有实现，未逐模式重新实测 | 不把测试存在视为当前部署所有模式实际验收通过；默认禁用边界要保留 |
| replay、backtest、walk-forward | 有实现与回归覆盖 | 当前自动真实数据研究路径失败，且多标的/周期合并合同不严 |
| VIX/risk 判断 | 已有真实研究和实现 | 是风险管理证据；不支持方向 alpha、全面收益提升的结论 |
| 新闻/基本面/信号研究 | 研究材料与入口部分齐备 | 缺失字段、PIT、真实数据获取和隔离环境仍未闭环；未发现可声明通过的交易优势 |
| journal、recovery、integrity | 热数据可重读 | 最老一代仍被删除；新增测试未越过保留上限，永久证据恢复未完成 |
| benchmark / evaluator | 可输出但不能作为最终验收 | 成本、零市场收益、窗口与资金基数方法需要修正 |
| 日报、自动研究 | 当前故障 | 两个实际定时服务 failed；timer active 不代表任务成功 |
| daemon、Ollama、watchdog | 当前运行 | autopoiesis 与 Ollama active；watchdog 上次 success；不等于所有服务健康 |
| 本地网页面板 | 初版 | 四页生成，缺两页与一致快照合同、自动更新和页面验收 |
| 公共项目网站与品牌设计 | 未完成发布 | README 不是已部署公共站；不应直接发布含私有账户数据的 runtime 面板 |
| 社区、许可证、贡献流程 | 基础齐备 | Apache-2.0、贡献指南、行为规范、SECURITY、issue/PR 模板已存在 |
| alpha release、发布包与外部上手 | 未验收 | GitHub releases 列表为空；无当前提交完整 fresh-clone PASS 和外部贡献者验收 |
| 60 日盈利与自演化效果 | 未完成 | 19 个交易日；当前未跑赢 SPY，LLM 增益未得到组合层面证明 |

## 7. 必须优先修复的具体问题

### P0：无效浮点配置可以削弱风控

`config.py:269` 的 `_positive_float` 和 `_optional_float` 只检查 `<= 0`，没有有限性检查。离线合成夹具复现：100 股、价格 100、正常限额 5000 时被拒绝；把限额设为 NaN 后，同一决策被批准。只测试 Guardian，没有调用 broker 或 executor，也没有改生产配置。这不表示当前正常配置下已经发生绕过，但表示“硬风控永远有效”的验收不成立。

最小改法：所有风险配置与关键金额/价格拒绝 NaN、±Inf；对程序直接构造配置的路径也设置不变量。验收：非法值启动失败，非法金额/价格 fail closed；正常有限值与已有风险规则保持原行为。

### P0：新安装与 CI 被新版 lint 阻断

按第 4 节修正字符串前缀实现/局部例外，建立可复现 dev 工具版本策略。验收：隔离克隆 install → check → smoke-offline → self-test → verify 顺序通过；当前 SHA 的 GitHub 三版本 job 全绿，不能用本机旧 Ruff 代替。

### P1：agent 扣成本公式错误

`tools/benchmark.py:262` 按“已实现利润 × 成本率”扣费，交易成本实际应来自成交名义金额。盈利低估成本，亏损甚至变成成本返还。

现有真实 paper 账本复算：realized 757.95，unrealized 98.64，gross 856.59；ledger 假定成本 17.909438，净额 838.680562；agent 段只扣 0.378975，报 856.21，差约 **17.53**。broker 记录费用为 0，17.91 是明确声明的假定交易成本，不能冒充真实收取费用。

复用 evaluator 的 lot/notional 成本计算，不保留第二套公式。补充正负 PnL、零市场收益（当前 `if not pnl.market_return_pct` 将合法 0 当缺失）、跨策略同时资金占用的合同。资金基数目前累加各策略峰值，不等于组合同时使用资金的峰值。验收：固定真实快照 CLI、日报、JSON、HTML 净额一致；不足 60 个交易日必须明确 insufficient，区间与基准一致。

### P1：日报和自动研究的实际入口坏了

- `daily_report.py:130` 仍调用 `_headline(report)`，新签名要求 agent 参数；命令与 `quant-report.service` 都出现同一 TypeError。测试通过没有覆盖此真实入口。
- `quant-research.service` 失败，实际 research.log 出现 `KeyError: 'bars'`。`cached_last_bar` 和 `_load_real_bars` 遍历 replay 所有 JSON，但目录同时有 CIK_MAP、FUNDAMENTALS、XS120 等非行情文件。
- 即使忽略 KeyError，读取器没有按 symbol/interval 选择，按时间戳合并不同数据可能串标的。不能靠宽泛 except 让任务表面成功。

验收：日报 Makefile 入口成功且数字一致；真实混合缓存按 schema、symbol、interval、来源选择；坏行情显式报错；service 的最近实际任务成功。research 依赖虽已声明 extra，但 research-install 仍安装到当前同一解释器环境，没有实现独立研究环境；缺少 fresh-clone 可执行的数据获取闭环。

### P1：轮转后的永久可追溯性没有完成

`journal.py:266–267` 在轮转时删除最老 backup，默认 64 MiB × 3 backups。当前 live 约 57 MiB、已有两个约 64 MiB backup。新增 rotation recovery 测试分别只强制一次轮转，没有越过备份数量上限，因此不能证明长期重建。README 的“唯一事实源，所有状态可重新生成”超过了目前保留保证。

先定义热保留与不可变归档；归档校验成功才删除热副本，失败保留唯一证据。验收：超过 backups 数量的多次轮转、损坏/归档失败、跨代恢复后，持仓、FIFO、策略生命周期和事件计数可重建；不能只证明未被删除的部分能重读。

### P2：面板不是已验收的完整前端

代码审查确认：分别读运行中 journal，未覆盖轮转代次，没有冻结共享快照；各页并非同一个数据版本；heartbeat 仅检查 RUNNING 字段，未核对年龄/PID；PAUSED 实际会被 daemon 有限重评估，却被页面写成 terminal；已知零个 trial 时可能显示 UNKNOWN；逐文件非原子写出。

`main(sys.argv[1:])` 后又读取 argv[1]，单个 `SITE=` 参数被忽略。未发现针对 status_page 的测试。缺 Portfolio & Risk、System、订单/成交完整追踪、统一 JSON/schema/commit/data hash、导出、自动生成与浏览器验收。页面可以保留静态 HTML；目前没有引入前端框架或 HTTP 交易接口的必要。

验收：一次生成一致脱敏快照，原子发布；覆盖真实/空/零/缺失/过期/错误状态；覆盖轮转日志；手机/桌面/键盘可用；无交易能力；公共项目站独立使用可公开证据，不发布私人运行目录。

## 8. 开源标准的准确判断

从许可与公开源代码角度已经是开源项目；从“外部用户按文档稳定使用、维护者可信发布”的角度仍未就绪。GitHub community profile 的 health 100% 只说明若干社区文件存在，不能证明安全、CI、收益或运行正确。

尚需：当前 SHA 的新环境证明、真实通过的 CI、发布包/版本与 alpha release、支持路径和已知限制、可用的私密漏洞报告渠道、公共站和截图。SECURITY 指向 GitHub 私密漏洞报告，本轮没有验证是否启用；行为规范也用泛指的“GitHub private reporting”处理行为投诉，需明确一个实际可用的私密联系渠道。依赖安全审查、外部机器与浏览器验收未在本轮完成，不应声称通过。

## 9. 剩余计划与验收顺序

以下待办按实际状态记录，不因为已有提交就自动划掉。实施遵循 AGENTS：先评价基线和成功标准、进入 Plan Mode、恢复要求的 Superpowers/planning-with-files 流程，再逐项改代码并验证。本轮报告不是代码实施门禁的替代品。

| 顺序 | 工作包 | 本轮状态 | 完成标准 |
|---|---|---|---|
| 1 | 非有限数值与 Guardian 不变量 | 待修，已复现 | NaN/Inf 无法弱化任何硬限额；正常配置回归通过 |
| 2 | Ruff / CI / fresh clone | 待修，已复现 | 当前提交三版本 CI 全绿；隔离完整流程通过、原始日志真实生成 |
| 3 | 收益成本共享计算与日报 | 部分实现但未验收 | 真实快照所有出口一致；日报命令与实际 timer 任务成功 |
| 4 | 混合缓存选择与自动研究 | 待修，实际服务失败 | 一标的一周期数据合同成立；真实 fetch→research→trial 闭环成功 |
| 5 | 退役配置清理 | 生产迁移基本完成，部署残留待清 | 旧 unit/config 引用清零；新指纹、模式、配置路径核对，不改历史证据 |
| 6 | journal 永久归档和恢复 | 仅热轮转测试完成 | 超保留上限恢复与归档失败情形通过 |
| 7 | 只读快照及六类面板 | 四页初版，未验收 | 统一数据版本、正确状态语义、两类缺页、自动更新和浏览器验收 |
| 8 | 当前路线图、社区与发布 | 社区文件已补，发布未完成 | 当前状态不与旧 TODO 冲突；外部上手成功、包与 alpha release 可复现 |
| 9 | 公共项目站与视觉设计 | 未完成 | 安装/架构/证据/贡献清楚；真实截图与可公开数据；响应式与可访问 |
| 10 | 60 交易日与 LLM/演化对照 | 19/60，未验证 | 完整固定窗口、风险匹配基准、净成本与统计不确定性；结果不足如实保留 |

工程优化放在正确性后：先合并 evaluator/benchmark/doctor 的重复计算和 journal 扫描，再按职责整理大模块；以实际耗时/峰值内存和相同真实快照的结果为基线，不为了模块数量重写架构。保留 Autopoiesis 名称，先完成这次迁移。实盘不属于下一阶段验收，也不能因 paper 有正 PnL 就开放。

## 10. 本次收益记录的解释

本轮 benchmark 覆盖 2060 cycles、19 个交易日、2026-06-09 至 2026-10-09 的离散记录；六个策略诊断均落后各自 SPY 基准。agent 报告约 1.89%，SPY 约 2.69%，超额约 -0.80 个百分点，但 agent 成本与资本口径尚有上述缺陷，不将此数字升级为最终绩效认证。

107 个可配对 LLM 决策，60 次 override，42 次局部探针优于规则；探针累计约 +18.320% 是单股 24h 动作比较，不是资金组合收益率。1077/2060 个决策无法归因，约 52%。账户所有者历史 29 股收益 +2821.65 单列排除。运行中的数据会继续变化；本轮不是冻结样本实验。

完成判断：**核心 paper 原型存在并运行；整体目标与可复现开源发布尚未完成。接下来先把风险、CI、测量与后台任务变成可信闭环，再交付完整前端和发布。**
