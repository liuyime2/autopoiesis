# 第一性原理重构与实验闭环计划 — 2026-11-10

本文件不继承任何已有 plan。已有文档只当作"发生过什么"的证据来源,不当作设计依据。
所有数字都来自 2026-11-10 当天在本机实际运行的命令(§1 给出来源)。本次只做了审查和测量,**没有改任何代码**。

---

## 0. 先说两件必须知道的事

1. **`master` 不是这个项目。** 真正在跑的是 `goal/edge-first`(727 次提交,比 `master` 多 323 次),线上 daemon 从 `releases/ba4a082fa383` 运行。`master` 停在 10-18,只有文档提交。任何"审查 master"的结论都是在审查一个没人运行的系统。本计划以 `goal/edge-first@ba4a082` 为基线。
2. **到目前为止没有任何证据表明系统有 edge。** 这不是代码质量问题,是研究结论,重构不会改变它(§2 F2)。计划必须允许"没有 edge"这个结果成立,并且论文的 claim 要在这种情况下依然站得住(§5)。

---

## 1. 基线(测量值,不是印象)

| 指标 | 基线 | 怎么测的 |
|---|---:|---|
| 生产代码 | 26,881 行 / 63 文件;其中代码 17,727,注释 3,080,docstring 3,253 | `ast` 逐行分类 `src/` |
| 测试 | 39,295 行 / 135 文件 / 2,224 个用例 | `pytest --collect-only` |
| `tools/` | 13,542 行 / 62 文件(含 15 个 systemd 单元) | `wc` |
| Makefile | 696 行 / 75 个 target | `grep -c` |
| 环境变量 | 60 个 `AUTOPOIESIS_*`;33 个文件直接读 `environ`(架构文档声称"只有 config.py 读") | `grep` |
| 最大文件 | `daemon.py` 2,501 行,`doctor.py` 1,969,`evaluator.py` 1,517 | `wc` |
| 全量测试(`master`,`make test`) | **17 分 42 秒;1,318 通过,11 失败**(`test_universe.py` 8 个,`test_config_wiring`、`test_replay_determinism`、`test_risk_judgment` 各 1 个;失败原因未诊断;同机另有一个项目的测试并行占用 CPU,耗时偏高) | 本次运行 |
| 一次离线实验(G4 v4:17 配置 × 8 标的 × 1,580 日线) | **30.0 s**;结果 SHA-256 `376bd9cc…` 与登记结果逐字节一致 | 本次重跑 `tools/run_portfolio_experiment.py` |
| 线上一个决策周期 | 53.2 s,其中模型 48.5 s | `reports/2026-11-10.txt` |
| `doctor` | 81 s(修复前 146–183 s) | `HANDOFF_2026-11-10.md` |
| Journal | 当前段 8.1 MB,其中 **98% 是两类派生快照事件**(`PNL_EVIDENCE_RECORDED` 均 499 KB/条,`STRATEGY_EVALUATION_RECORDED` 均 388 KB/条);真正的 CYCLE 记录 2 KB/条 | 逐行解析 `journal.jsonl` |
| 分支/部署 | 6 个分支,4 个 worktree,21 个 release 检出(208 MB),`runtime/backups` 72 GB | `git worktree list`,`du` |
| 策略库 | 169 个:PAUSED 102,RETIRED 58,PROBATION 8,BASELINE 1,**ACTIVE 0** | 读 `runtime/autopoiesis/strategies/*.json` |
| 收益 | agent −0.13% vs SPY +1.55%(2026-06-09 → 11-10),落后 1.69 点;模型归因 −105.11 美元,baseline +564.39 | `reports/2026-11-10.txt` |
| 假设检验 | 61 个预测假设,0 个存活;G4 v4 `NOT_PROVEN`(B−A Holm p=0.054,且低于同敞口买入持有与全部 10 个随机择时种子) | `docs/evidence/` |

未测量、不下结论的:`goal/edge-first` 上的全量测试耗时与通过数;lint/mypy 当前数字。这两项是 P0 的第一步。

---

## 2. 诊断:十个发现

**F1 没有单一事实来源。** 代码有 6 个分支,文档有 20+ 份带日期的 plan/handoff/summary,`master` 与线上相差 323 次提交。新人 clone 默认分支得到的是一个不运行的版本。

**F2 没有 edge,而自进化环在一个不可能产生 edge 的空间里搜索。** 169 个策略里 95 个 TREND_FOLLOW、40 个 FIXED_SIZE、33 个 RULE——模型四个月的"自进化"产出是同一族规则的参数变体,0 个晋升到 ACTIVE。11-10 当天 150 个周期全部 HOLD,其中 18 次"override"全是同一句话("没有持仓所以不能 SELL")。这是一个空转的环。

**F3 被评估的对象 ≠ 被执行的对象。** `offline_validation.py` 自己写明:策略的规则在生产路径上不被强制,策略只是交给模型的上下文,最终是模型决定。于是有两条互不相通的评估线:规则回测(评的是没人运行的规则)和决策记分(评的是模型,样本每天 ~150 个且几乎全是 HOLD)。**任何优化都需要"提案的东西 = 评估的东西 = 上线的东西"**,现在三者是三样东西。

**F4 同一件事("在历史数据上跑一个决策规则并打分")有至少 7 套实现。** `research/backtest`、`research/walk_forward`、`research/experiment`、`research/portfolio_experiment`、`replay`、`offline_validation`、`counterfactual`,加上 `docs/evidence/` 下 2,900 行一次性脚本。SMA/规则信号有 4 处实现(`strategy_engine.rule_signal`、`backtest._rule`、`experiment.SmaEngine`、`portfolio_experiment.SmaBand`)。G4 v3 用了未复权数据而 v4 才发现,正是多条路径各自取数的后果。

**F5 迭代闭环太慢,慢在三处。** 线上反馈:一天 ~150 个样本,一个周期 53 s。离线反馈:17 个配置 30 s(每根 bar 走一遍 pydantic + Guardian),一次像样的搜索(10⁴ 配置)要 5 小时。开发反馈:测试 >12 分钟。第 8 条原则("infra first, loop fast")目前三处都不满足。

**F6 Journal 把派生状态写进了只追加日志。** 每小时把全量 PnL 归因和策略指标重新序列化一遍(~0.9 MB/次),占日志 98%。这就是 `doctor` 要 81 秒、journal 每两天滚 67 MB、备份 72 GB 的根因。事实日志应该只有事实;派生量应该可以从事实重算。

**F7 配置、入口、脚本分散。** 60 个环境变量、75 个 make target、62 个 tools 文件、5 份实验 spec 格式、15 个 systemd 单元。"怎么跑一个实验"没有唯一答案。

**F8 `daemon.py` 是把 workflow 写死在代码里的上帝对象。** `_maintenance()` 依次调用:对账 → 证据 → PnL → 反事实 → 校准 → 课程淘汰 → 离线筛 → 反思 → 课程提案 → 生命周期。这**就是**一条 Chain of Tasks,只是它是 2,501 行 Python 而不是数据,所以系统无法检查它、消融它、或者改它。你想要的"自我优化 workflow"在当前结构下没有可以优化的对象。

**F9 "小模型/工具"已经存在,但各自是孤岛。** `regime`、`risk_judgment`(波动率预测)、`calibration`、`sleeve`、`signal_test`、`attention`、`information`、fast-brain(logit 读取)各有各的输入输出、各的接线方式,加一个要改 daemon、cli、config、doctor 四处。模型调用有 3 个 transport(`cli.py` 两个 + `llm_decision.py`)。

**F10 有些模块的产出从未被打分,因此无法判断去留。** 典型是 `attention.py`:模型从候选里选要看的标的,但被选中的"仅注意力"标的不进入反事实评分,上线第一天 9 次选择全部回退到机械列表而无人察觉。同类的还有 reflection、knowledge lesson、calibration lesson。本计划不为它们单独做优化,统一按一条规则处理:搬成插件,进留一消融(E6),过不了就删。

**注释问题单列。** `src` 里 26% 的非空行是注释和 docstring,大部分是在叙述历史缺陷("以前这里是…,测得…")。那是 commit message 的内容。它让每个文件读起来像事故报告,真正的不变量被淹没。

---

## 3. 第一性原理:这个项目到底在解决什么

去掉所有历史,问题只有一句:

> **给定只追加的真实观测,找到一个决策程序,使其在未见数据上、扣除成本后、在硬风险约束内,优于"什么都不做"的基准;并且让寻找这个程序的过程本身可以被自动改进,且不自欺。**

拆开只有五样东西是必要的:

| 必要件 | 它是什么 | 现在对应的东西 |
|---|---|---|
| **观测** | 带 `available_at` 的时点数据,一个读取口 | `data_gateway` + `replay` 缓存 + `information` + 4 个 refresh 脚本 + evidence 脚本各自取数 |
| **程序** | 一个可序列化的决策程序(下称 Chain) | `StrategySpec` + 模型 prompt + policy_engine + sleeve + attention,散在各处 |
| **评估器** | 一个函数:`(Chain, 数据, 成本) → 逐日收益`,加一套统计 | 7 套 |
| **账本** | 只追加:事实(live)和试验(research) | journal(混入派生量)+ 3 个 trials 文件 + registry + lineage |
| **改进器** | 读账本,提出新 Chain | `curriculum`(只能提 4 种 kind 的参数) |

外加上线必需且**不可动**的三件:`guardian`、`executor`+对账、market-open 检查。它们是安全边界(AGENTS §17),本计划不改变其语义,只搬位置。

**核心执行路径只有一条,线上线下相同:**

```
观测(t) → Chain.run → 目标仓位 → Guardian → 执行/模拟成交 → 账本
                                                    ↓
              改进器 ← 评估器(账本, 数据) ← ────────┘
```

线上与离线的差别只允许是两个替换件:时钟和成交。这一点现有 `replay.py` 的思路是对的,保留。

---

## 4. 目标系统

### 4.1 三个概念,各只有一种表示

**Plugin(插件)** — 一个纯函数加一份声明:

```python
@plugin(name="sma_band", kind="signal", inputs=["close"], params={"n": int, "band": float})
def sma_band(view: PointInTimeView, n: int, band: float) -> float: ...
```

- `kind ∈ {source, feature, signal, selector, sizer, judge}`。`selector` 选标的(现 attention);`judge` 是 LLM 节点。
- `view` 只能读到 `available_at ≤ t` 的数据,物理上读不到未来——泄漏由类型保证,不靠纪律。
- 现有的"小模型/工具"全部搬成插件:`regime`、`risk_judgment`、`calibration`、`sleeve`、SMA/动量/规则、`attention`、fast-brain、VIX/基本面/新闻源。**加一个插件 = 加一个文件**,不碰 daemon/cli/config。
- 验收:新增一个插件并在实验里使用,diff 只包含一个新文件和一行 Chain 配置。

**Chain(任务链)** — 一个 DAG 的数据描述(JSON),节点是插件调用:

```json
{"id": "core-v1", "nodes": [
  {"id": "watch",  "use": "attention_llm",  "params": {"k": 4}},
  {"id": "trend",  "use": "sma_band",       "params": {"n": 20, "band": 0.01}},
  {"id": "vol",    "use": "vol_forecast"},
  {"id": "decide", "use": "llm_judge",      "inputs": ["trend", "vol"]},
  {"id": "size",   "use": "vol_target",     "inputs": ["decide", "vol"]}
]}
```

- **Chain 取代 `StrategySpec` 的四种 kind、sleeve 配置、attention 开关、"规则 vs 模型"的二分。** 纯规则策略是没有 judge 节点的 Chain;当前线上行为是一条带 judge 的 Chain。
- 这解决 F3:被提案的、被评估的、被上线的是同一个 JSON,用内容哈希标识。
- 有两层 Chain,同一种机制:**决策链**(每个周期跑,产出仓位)和**改进链**(盘后跑,产出新的决策链)。F8 里 `_maintenance()` 的那串调用变成一条改进链的数据。**"自我优化 workflow" = 改进器的搜索空间包含改进链本身。**

**Trial(试验)** — 账本里的一行:`{chain_hash, data_hash, evaluator_hash, split, metrics, proposer, parent, knowledge_refs}`。合并现有的 `research_trials.jsonl`、`signal_trials.jsonl`、`experiment_registry`、`lineage`、prereg。多重检验校正直接从这一个账本计数。

### 4.2 一个评估器,两档速度

- **快档(向量化)**:无 judge 节点的 Chain 在日线/5 分钟线上整列计算。目标:G4 v4 的 17 配置 < 1 s(现 30 s)。
- **忠实档(replay)**:逐 bar 走真实 Guardian,下一根开盘成交。保留现有 `replay.py` 语义。带 LLM judge 的 Chain 只能走这一档,模型输出按 `(prompt_hash, model)` 缓存,使重跑确定且免费。
- **等价性是硬验收**:同一 Chain 两档产出的逐日收益一致(容差 1e-9);忠实档在 G4 v4 上重现 `376bd9cc…`。先有这个金标准测试,再删旧实现。
- 统计只有一份:配对 block bootstrap、Holm、DSR、PBO(现 `research/statistics.py`,保留),加上 HANDOFF 里欠着的批量 FDR(BH/BY)。

### 4.3 改进器:经验主义与唯理派各做各的事

你的区分是对的,而且它直接决定分工:

| | 经验派(小模型/统计插件) | 唯理派(LLM) |
|---|---|---|
| 擅长 | 从大量样本估计一个数;便宜、可回测、可校准 | 提假设、读文本、分解问题、迁移知识 |
| 不擅长 | 提出新结构 | 估计数值——已测:5 个模型方向预测 Spearman 全在 ±0.03 内,Brier 全部差于均匀猜测 |
| 在系统里的位置 | 决策链里的 feature/signal/sizer 节点 | **改进链**里的提案者;决策链里至多做 selector/judge,且必须由消融证明有用 |

所以默认设计是:**LLM 不直接预测方向,LLM 设计由经验派插件组成的 Chain,由评估器裁决。** 这把一个周期 48 s 的模型调用从热路径移到盘后,同时把每天 150 个样本的学习信号换成每次提案上千根 bar 的回测信号。这是对 F2/F5 的结构性回答,不是调参。

改进链(初版,固定;之后成为被搜索对象):

```
诊断(读账本:哪里亏、哪类试验失败) → 检索知识 → 提出假设(预登记进账本)
  → 编译成 Chain → 快档筛 → 忠实档确认 → 统计闸门 → 影子/试用 → 晋升或淘汰 → 写回知识
```

### 4.4 知识与联网搜索

- **知识库 = 账本上的索引,不是另一个存储。** 一条知识是 `{claim, 来源, available_at, 支持/反驳它的 trial 列表}`。"学到了"的定义是:后续提案引用它,且引用它的提案通过率高于不引用的(可测,见 C5)。失败的试验同样是知识(负结果防止重复提案——现在 60 条提案里曾有 51 条是重复 id)。
- **联网搜索只在改进链里、只在盘后、只产出假设。** 搜到的论文/因子进入"提出假设"一步,必须经过与其他假设完全相同的评估闸门,并计入多重检验次数。搜索结果带抓取时间;回测时 `available_at` 晚于回测时点的知识不可见。
- **安全边界(AGENTS §17)**:LLM 产出的是 Chain JSON(数据),不是代码,不是 shell。新插件(代码)由人或开发会话写、走 PR 和测试;改进器可以**提出**"需要一个什么插件",落到 backlog,不能自己执行生成的代码。网页内容一律当数据,不当指令。

### 4.5 仓库形态

```
src/autopoiesis/
  core/      types.py  ledger.py  data.py(时点视图)  config.py(唯一读配置处)
  plugins/   一个文件一个插件 + registry.py
  chain/     spec.py  run.py
  eval/      fast.py  replay.py  stats.py  trials.py
  improve/   proposer.py  knowledge.py  search.py
  live/      daemon.py(调度+心跳,<400 行)  guardian.py  executor.py  reconcile.py
  cli.py     run | eval | improve | status   四个子命令
configs/     一个 TOML(线上)+ chains/*.json + experiments/*.toml(一种格式)
docs/        README  ARCHITECTURE  EXPERIMENTS  + evidence/(只放可重跑的结果)
```

目标数字(验收用):

| | 现在 | 目标 |
|---|---:|---:|
| `src` 代码行 | 17,727 | ≤ 9,000 |
| 最大文件 | 2,501 | ≤ 600 |
| 评估实现 | 7 | 1(两档) |
| `tools/` 文件 | 62 | ≤ 8(deploy、systemd、fresh-clone) |
| make target | 75 | ≤ 12 |
| 配置项 | 60 个环境变量 | 一个文件,≤ 25 项;密钥仍只在 `~/.config/autopoiesis/env` |
| 快速测试 | >12 min | 单元层 < 60 s;全量 < 5 min |
| 17 配置实验 | 30 s | < 1 s |
| `doctor`/status | 81 s | < 5 s |
| journal 行均大小 | 快照 ~450 KB | < 5 KB,派生量可重算 |
| 分支 | 6 + 21 release 检出 | `main` + 短命分支;release 用 tag |

行数是约束不是目标(AGENTS §7 的顺序:删 → 合 → 简 → 复用 → 重写 → 加)。测试 39k 行里大量是在钉住将被删除模块的内部细节,会随模块一起删;保留的是行为层金标准。

---

## 5. 论文 claim 与实验设计

### 5.1 先把 claim 定对

基于 §1 的证据,"我们的系统赚钱"不是一个现在能辩护的 claim,把论文押在它上面等于押在一个 61 战 0 胜的命题上。能被实验闭环支持、并且不依赖市场给不给面子的 claim 是关于**改进过程本身**的:

| # | Claim | 一句话 |
|---|---|---|
| C1 | **有效性(不自欺)** | 在没有信号的数据上,这个 RSI 环晋升的东西不超过名义 α |
| C2 | **功效(找得到)** | 在植入已知大小信号的数据上,环在给定预算内以可测的概率找到它 |
| C3 | **Chain of Tasks 分解有用** | 同等预算下,分解的改进链优于单次大 prompt |
| C4 | **LLM 提案优于无知识搜索** | 同等评估预算下,LLM 提案者优于随机搜索和网格/贝叶斯搜索 |
| C5 | **持续学习有用** | 有知识库(及联网检索)的提案者,通过率和所需试验数优于没有的;且随轮次改善 |
| C6 | **可组合性与每个部件都有用** | 每个保留在最终系统里的插件/环节,去掉它都会显著变差 |
| C7 | **经验派 × 唯理派分工** | LLM 设计 + 小模型估计 > 纯 LLM 决策,也 > 纯小模型搜索 |
| C8 | **真实市场结果(如实报告)** | 锁定样本外 + 纸面前瞻上相对同敞口买入持有的扣费超额,无论正负 |

C1+C2 是这篇论文的地基:它们把"自进化"从一个故事变成一个有 I 类错误率和功效曲线的测量仪器。C8 为正是加分项,为负不推翻 C1–C7。

### 5.2 数据切分(现在就冻结,写进账本)

| 段 | 区间 | 用途 |
|---|---|---|
| 开发 | 2020-07-27 → 2024-12-31 | 改进环随便用 |
| 验证 | 2025-01-01 → 2025-12-31 | 环内的统计闸门读它 |
| **锁定** | 2026-01-01 → 2026-11-06 | 每个实验臂**只读一次**,读取动作记入账本 |
| 前瞻 | 部署日之后的纸面交易 | 唯一真正未见的数据 |

说明一个事实:2020–2026 这段历史已被 G4 v2–v4 看过多次,"锁定"段不是处女地。因此 C8 的权重放在前瞻段,C1–C7 放在合成对照上(那里的真值是已知的)。

合成对照(**明确标注为合成,只用于校验仪器,不作为真实收益的证据**,符合 AGENTS §5):
- **零数据**:真实 bar 按日做 block 置换/符号翻转,保留波动结构,摧毁可预测性。
- **植入数据**:在零数据上注入已知规则的已知强度信号(年化超额 2% / 5% / 10%)。

### 5.3 实验矩阵

统一规定:预算 = 评估器调用次数与模型 token 两个上限,各臂相同;每个配置 ≥ 20 个种子;报告配对 block bootstrap 的 95% CI 与 Holm 校正 p;所有试验(含失败)进账本;预登记 spec 先提交再跑。

| 实验 | 支持 | 臂 | 主指标 | 通过标准 | 证伪条件 |
|---|---|---|---|---|---|
| **E0 等价** | 基础设施 | 新评估器 vs 旧 replay | 逐日收益差;G4 v4 结果哈希 | 一致;快档 ≥30× | 任何不一致 → 不许删旧实现 |
| **E1 零校准** | C1 | 完整环跑在 200 份零数据上 | 晋升率 | 晋升率 ≤ α=5%(单侧二项上界) | > α → 闸门有洞,先修再做别的 |
| **E2 功效** | C2 | 完整环跑在植入数据上,3 个强度 × 预算 | 找回率、找回所需试验数 | 5% 强度下找回率 ≥ 80% | 10% 强度都找不回 → 环无用 |
| **E3 分解** | C3 | 改进链(分步)vs 单 prompt 直接出 Chain vs 去掉"诊断"步 vs 去掉"检索"步 | E2 的找回率 + 真实验证段最优 J | 分步显著优于单 prompt | 无差异 → 删分解,保留单步 |
| **E4 提案者** | C4, C7 | LLM 提案 / 随机 / 网格 / TPE / LLM 直接当决策者(现状) | 同预算下验证段 J;E2 找回曲线 | LLM 提案 > 随机与 TPE | LLM ≤ TPE → 提案者换成 TPE,LLM 只做假设来源 |
| **E5 知识** | C5 | 无知识 / 有知识库 / +联网检索 / 知识打乱(安慰剂) | 提案通过率、重复提案率、达到同 J 的试验数、随轮次斜率 | 有知识 > 无 > 打乱不优于无 | 打乱 ≈ 真实 → 知识通道是装饰,删 |
| **E6 留一消融** | C6 | 最终 Chain 与最终改进链,逐个去掉每个节点/每个环节(含 attention、judge、reflection、knowledge、calibration) | ΔJ(验证段)+ E2 找回率变化 | 每个保留项 ΔJ 的 CI 不含 0 | CI 含 0 的部件**删除**,更新最终系统后重跑 |
| **E7 自优化 workflow** | C3 延伸 | 固定改进链 vs 允许改进器改改进链(受限编辑:增删换节点) | 多轮后的 E2 找回率与真实验证段 J | 自改版在后半程显著更好,且 E1 仍 ≤ α | E1 被破坏 → 元层必须受闸门约束,报告为负结果 |
| **E8 真实** | C8 | 最终系统 vs A(核心 sleeve)vs 同敞口买入持有 vs 随机择时 | 锁定段读一次;前瞻段 ≥ 60 个交易日 | 相对同敞口买入持有的日超额 CI 下界 > 预登记 `min_effect` | 如实报告;不改阈值 |

**目标函数 J**(全程唯一):相对**同敞口买入持有**的扣费日超额收益均值,附最大回撤约束。选这个基准是因为 G4 v4 已经证明"相对核心 sleeve 的超额"可以完全由"多持有了上涨的股票"解释。

**消融的纪律(E6):** 最终系统不是设计出来的,是消融剩下来的。任何一个模块——包括 attention、reflection、knowledge、calibration lesson、judge 节点、甚至改进链里的某一步——如果去掉它指标不变差,它就不在最终系统里,也不在论文的系统图里。按 §2 的证据,我的先验是 judge 节点和现有 knowledge lesson 通道过不了这一关;这要由 E6 说了算,不由我说了算。

### 5.4 每个实验会产出的"观察"

实验报告固定四栏,防止只报喜:**设了什么 / 测到什么(数字+CI)/ 排除了什么解释(对照臂)/ 没能说明什么**。现有 `g4-v4-results` 的写法就是这个标准,沿用。

---

## 6. 执行顺序

每一阶段是一个可独立验收的闭环;前一阶段的验收不过,不开下一阶段。全程线上 daemon 继续跑现有 release,**重构在新目录里长出来,不在线上代码里原地改**。

**P0 统一事实来源(约 1 天)**
- 把 `goal/edge-first` 定为主干;`master`/`codex/*`/`split-a-identity` 打归档 tag 后删除;21 个 release 检出改为 tag(保留线上那一个)。**这一步涉及删分支,需要你明确同意后才做。**
- 在主干上测全基线:全量测试耗时与通过数、lint、mypy、`fresh-clone`。填进 §1。
- 验收:`git clone` 默认分支 → `make install && make test` 通过;§1 无"未测量"项。

**P1 金标准与最小闭环(2–3 天)**
- 写 E0 的行为层金标准:G4 v4 哈希;一段真实 journal 的逐周期决策回放;Guardian 的全部拒绝原因各一例。这些测试只依赖公开输入输出,不依赖内部结构。
- 新目录里打通 `install → data → run → evaluate → output`:一个插件(sma_band)、一条 Chain、快档评估、写一行 Trial。
- 验收:`autopoiesis eval configs/chains/sma20.json` 在 < 1 s 内输出与旧实现一致的逐日收益。

**P2 一个评估器(3–5 天)**
- 快档 + 忠实档 + 统计 + Trial 账本;时点数据视图(含 VIX/FRED/EDGAR 的 `available_at`,这是 HANDOFF 里欠着的)。
- 共享现金账本(修掉 G4 v4 里 −52 美元的子账本负现金)。
- 验收:E0 全过 → **删除** `research/backtest`、`walk_forward`、`experiment`、`portfolio_experiment`、`offline_validation` 的重复部分和 `docs/evidence/**/*.py`。删除后金标准仍过。

**P3 插件与 Chain(4–6 天)**
- 把 F9 列的每个小模型搬成插件;`StrategySpec` 四种 kind → Chain;线上决策路径改为"跑一条 Chain"。
- journal 只记事实,派生量改为按需重算并缓存(F6)。
- 验收:线上行为等价(同一段真实 journal 回放,决策逐条一致);`daemon.py` < 400 行;加一个插件的 diff 只有一个文件。先 shadow 一个交易日,再替换。

**P4 删(2–3 天)**
- 配置收成一个文件;make target、tools、systemd 单元按 §4.5 收敛;历史叙述型注释删除(内容已在 git log);带日期的 plan/handoff 文档移入 `docs/history/` 一个归档。
- 验收:§4.5 目标表逐项达标;`run-fresh-clone.sh` 通过。

**P5 改进器(5–7 天)**
- 改进链初版、知识索引、联网检索(盘后、只产出假设)、提案者的四个对照实现(随机/网格/TPE/LLM)。
- 验收:E1 通过(零数据上晋升率 ≤ 5%)。**E1 不过,后面的实验一个都不跑**——一个会在噪声上晋升的环,它的任何正结果都不可信。

**P6 实验(2–3 周,主要是机器时间)**
- 顺序:E2 → E4 → E3 → E5 → E6 → 按 E6 结果收缩系统 → 重跑 E1/E2 确认 → E7 → E8 锁定段读一次。
- GPU 5–9 空闲,可并行跑种子;模型输出缓存使重跑免费。
- 验收:每个实验有预登记 spec、可重跑命令、结果哈希、四栏报告。

**P7 前瞻与成文**
- 最终系统纸面运行 ≥ 60 个交易日(E8 前瞻段);期间不改 Chain 的晋升规则。
- 论文系统图 = E6 之后剩下的东西。
- 实盘仍然禁止(AGENTS §16),本计划不改变这一点。

---

## 7. 风险与需要你决定的事

1. **是否同意把 `goal/edge-first` 定为主干并归档删除其余分支?**(P0,不可轻易撤销,未获同意不执行)
2. **是否接受 §5.1 的 claim 重心——论文立在 C1–C7(过程的有效性与功效),盈利(C8)如实报告但不作为立论前提?** 如果你要求盈利必须是核心 claim,那么按现有证据,诚实的预期是这篇论文目前写不出来。
3. **重构期间线上是否继续跑现有 release?** 我的建议是继续跑、只修安全相关缺陷,不再给旧结构加功能。
4. 风险:E4 可能显示 LLM 提案不优于 TPE。那是一个可发表的负结果,系统会相应变简单(LLM 只做假设来源),不是计划失败。
5. 风险:P3 的线上切换。缓解:决策逐条等价回放 + 一个交易日 shadow + Guardian/执行器代码原样搬迁、不重写。
6. 已知的现存安全缺口(来自 HANDOFF,不属于重构但不应被重构掩盖):Guardian 的 500 美元日亏损限额只停新单,不限制已持仓亏损(G4 子账本单日最大 −1.8k)。这是账户持有人的决定项。
