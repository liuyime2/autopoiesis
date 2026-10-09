# 2026-10-09 项目审查结论与执行记录

本文件回答两个问题：**计划里说了什么**，以及**哪些已经落地、哪些没有**。它是对
`docs/history/plans/2026-10-09-project-review-and-roadmap.md` 的逐条核对，不是那份计划的替代品。

每一条断言都标了三类之一：

- **已修** — 有命令跑过、有输出，命令列在条目后面；
- **已测未融合** — 测了，结论是不做，理由写清楚；
- **未做** — 没动，原因和下一步写清楚。

基线：计划审查基于 `55d673a`；本文件完成后 HEAD 为 `d5de826`，共 4 个提交。远端跟踪分支未重新
fetch，因此不据此断言 GitHub 当前状态。

---

## 1. 计划里最重要的一句话

> 不以"完成 80%"描述此项目：工程链路、策略有效性、开放贡献体验、产品界面是四个不同维度。

本节同意并按此组织。当前状态：

| 维度 | 状态 | 证据 |
|---|---|---|
| 工程链路 | **可用** | `make check` exit 0，1139 测试，fresh-clone 9/9 |
| 策略有效性 | **未验证** | `make benchmark` 19 个交易日，5/6 策略跑输买入持有 |
| 开放贡献体验 | **仍破损** | 见第 3 节 P1/P2 |
| 产品界面 | **不存在** | 无前端文件，计划 §6 未开始 |

---

## 2. 已修的：全部 P0，和计划预警的一个真缺陷

### 2.1 改名迁移的最后一层（计划 §4 P0-1/2/3/4）

计划审查时 `make check`、`make smoke-offline`、`make run/stop` 三条都失败在已删除的
`min_agent` 模块上。本轮全部修完：

```
$ make check
All checks passed!            (ruff)
Success: no issues found in 45 source files (mypy)
1135 passed, 1 skipped in 101.43s
EXIT=0

$ make smoke-offline
import ok
offline smoke ok: package imports, config loads, CLI runs with no broker
EXIT=0
```

`make run / stop / restart` 此前指向 `min-agent.service`，而 `minictrl install-service` 渲染并启用
的是 `autopoiesis.service` —— 文档里的控制命令指向一个安装器从不创建的 unit。三处 systemctl、
`Makefile` 的 12 处 `runtime/min_agent`、CI 的 mypy 路径与 import、`ruff.toml` 的
`known-first-party`，一并改正。

`tools/verify.py` 的指纹告警此前告诉 operator"重启 min-agent.service"，已改。

### 2.2 fresh-clone 证据首次覆盖用户真实路径（计划 §4 P0-5）

计划指出："fresh-clone 脚本未跑 make check / type，也未跑 Makefile 离线入口"。此前它只跑裸命令，
而 README 教新人的是 `make install` → `make check`。

现在 9 步，含 `make check` 和 `make smoke-offline`：

```
$ docs/evidence/run-fresh-clone.sh
fresh clone: all steps pass, log at docs/evidence/fresh-clone.log
RESULT: PASS × 9    FAIL × 0
```

**这里有一次失败的运行，值得记录**：第一次跑仍是旧路径报错。原因是 fresh-clone 克隆的是
**已提交的树**，而修复当时未提交——脚本测的是 commit 不是工作区。这是脚本在正确地做事，也是
它值得存在的原因：绿的工作区 ≠ 绿的克隆。修完后重跑才得到上面的 9/9。

日志由重新运行生成，**没有手工改写**（AGENTS.md §5：它是 transcript）。

### 2.3 type 门的措辞（计划 §4 P0-6）

计划："verify 的 type 门只查 Makefile 文本是否包含 mypy 和依赖连接，并不执行类型检查；'配置正确'
不能写成'类型通过'"。

`type-checking-is-a-gate` 按其设计只断言接线（`type:` 不以 `-` 开头、真的调用 mypy、`check:` 依赖
`type:`），它从不运行 mypy。它的 PASS 文案读起来像在证明类型没问题，已改为明确说它不证明什么：

> `type:` runs mypy and blocks; `check:` runs lint, type and test. This class asserts the wiring
> only — it does not run mypy, and no verdict here says the types pass.

mypy 由 `make check` 运行，因此由 CI 运行，也由 fresh-clone 运行——这正是计划要求的。

### 2.4 计划预警的一个真缺陷：VIX 缓存的日期（计划 §4 P1）

计划在我写 VIX 代码**之前**就指出："未提交 VIX 代码取缓存最后一点，未检查其日期"。

这个缺陷的方向不是崩溃而是**错向**：一个冻结在平静周的缓存会在一路飙升中一直说"平静"，于是
VIX 下限——它存在的唯一目的是增加谨慎——变成了维持大仓位的理由。已修为四种拒绝：读数超过 4 天、
日期在未来、日期不可解析、以及"够新且够高"时正常生效。最后一种必须存在，否则一个永不放行的
守卫就是穿着守卫外衣的永久禁用。

四个新测试，23 个 risk-judgment 测试，1139 全过。

---

## 3. 测了，结论是不融合

### 3.1 VIX 混入预测：**不一致，不融合**（计划 §8 推荐顺序 2）

| | EWMA（现用） | VIX 单独 | 混合 |
|---|---|---|---|
| SPY | 0.228 | **0.264** | 0.260 |
| TLT | **0.384** | 0.193 | 0.375 |
| XLE | 0.417 | **0.430** | 0.417 |
| XLF | 0.267 | **0.279** | 0.277 |

2/4 略胜、1/4 落后。项目自己的规则把"不一致"判为不融合，因此**没有把 VIX 混进预测**。一个只增加
复杂度而不改善结果的第二输入，比现在这一套更糟。

### 3.2 VIX 作为下限：**4/4 一致，融合**

换成问"VIX 是否比系统自己的预测**更早**发现波动冲击"——这是风险系统该问的问题——结果单边且一致：

| 标的 | 不对称性 | rho | p |
|---|---|---|---|
| SPY | +12.91 | +0.304 | 0.0004 |
| TLT | +1.40 | +0.183 | 0.0056 |
| XLE | +10.29 | +0.241 | 0.0004 |
| XLF | +19.36 | +0.427 | 0.0004 |

所以 `vix_floor` 只做 `max(预测, VIX 缩放)`。**方向是重点**：下限只能抬高预测，抬高预测只能缩小仓位。
用抬高的 VIX 做机制检查（非证据）：SPY 仓位 1.000→0.921，XLF 0.977→0.810，从未变大。
`floored_by_vix` / `own_ewma_vol_pct` / `vix_note` 随 block 输出，读者能看见数字由谁产生并反驳它。

VIX 混入与 VIX 下限两个结果都记在台账里，因为**同一个数据源给出两个不同答案**，只留一个会误导。

### 3.3 六脉神剑：**触发率 0.27%，不可融合**

MACD+KDJ+RSI+LWR+BBI+MTM 六项共振。8481 根 SPY 日线、1993 至今：

```
六项全同      23 根 = 0.27%（2 看多，21 看空）
五项一致       0 根
四项一致    3145 根
三项一致       0 根
RSI 与 LWR   87% 的 K 线相反
```

两条来自测量的结论：触发率 0.27%，33 年里只 2 次看多，任何置信度下都不可交易；且分布双峰
（3 项和 5 项都是 0 根），六个指标实际塌缩成约两个有效状态。RSI 与 LWR 87% 相反，因为 %R 是
反向振荡器——所谓"六个独立信号"接近四个。台账记为 `UNDERPOWERED` 并附理由（`agreement_bars_below_30`）
和 bar 数，不伪装成一个 null。

### 3.4 基本面缺口已补，但不是运行时依赖

券商**没有**财报端点：`/v2/calendar/earnings`、`/v1beta1/eps/estimates`、
`/v1beta1/financials/entities/earnings-metrics` 三个全 404，而同一凭证下 `/v1beta1/news` 正常。
Yahoo Finance 补上：INTC 最后 10-Q 2026-07-23，预期 0.22、实际 0.42、意外 +94.59%；下次财报
2026-10-29（尚未发生，可知）；另有板块/行业/市值/机构持股和四档带分析师人数的一致预期。

未加为运行时依赖：yfinance 带入 `websockets 16.1.1`，与 alpaca 的 `<11` 冲突，也会毁掉
"3 个运行时依赖 / 干净克隆秒装"这个真实优势。抓取在研究侧离线做、写缓存，生产只读缓存。
`src/` 不 import yfinance。

---

## 4. 未做的：计划里 P1 余项、P2、以及 §6/§7 全部

按计划自己的顺序列，未做的原因写清楚。

| 计划项 | 状态 | 为什么没做 / 下一步 |
|---|---|---|
| P1 benchmark 从 doctor 文案解析，口径不一致 | **未做** | 计划自己说"先明确计算合同"。这是阶段 B 的核心，需要独立一轮 |
| P1 报告各段读取时间不同 | **未做** | 同上，属"可信测量"工作包 |
| P1 journal 64MiB/3 备份，README 称唯一事实源 | **未做** | 需要归档恢复验收，不能只改文字 |
| P1 成交价格未带成交时间进新鲜度判断 | **未做** | 需要 broker 故障注入复现，本轮未做故障注入 |
| P1 研究脚本依赖未声明在 pyproject | **未做** | `research` 可选依赖 + make 入口 |
| P2 README/ARCHITECTURE 计数 | **部分** | ARCHITECTURE 已改到实测值（43 文件/5 research/cli 26 导入/models 23 导入）；README 43 模块/23 导入/76 测试经门禁核对已正确 |
| P2 ruff 计数 75/76 漂移 | **已修** | 见下 |
| P2 大文件拆分（doctor 1585、daemon 1627、strategy_engine 1029、verify 3525） | **未做** | 计划说"先找重复计算并合并" |
| §6 网页前端 | **未开始** | 阶段 D，5-8 工作日 |
| §7 开源社区文件 | **未开始** | 阶段 E |
| 阶段 F 60 交易日观察 | **进行中** | 现 19/60 |

### 关于那个 75/76 漂移

计划的指令是"根据实际行为审查新增捕获是否妥当，**再**更新或移除易漂移硬编码计数"。审查后
VIX floor 的 `except Exception` 收窄为 `(OSError, ValueError, KeyError, TypeError)`——四个就是它
真实的失败模式。计数回到 75 且不再需要手改，19 个测试仍全过。**改行为，不是改数字。**

---

## 5. 计划 §5 的名字结论，本轮确认

计划建议保留 `Autopoiesis` 一次迁完、不再改第二次。本轮把迁移补完了：

`git shortlog -sne` 现在显示 328 个提交归到一个发布身份。用的是 `.mailmap`（已 tracked）而不是
`git filter-repo`——后者会重写全部 316 个 hash 且不可逆，还会切断与 `origin/main` 的链接。
旧身份用 `--no-use-mailmap` 仍可读。

历史文档 `docs/history/` 与 `docs/evidence/*.log` **未改写**：前者是档案，后者是 transcript，
改写即使之成为伪造（AGENTS.md §5）。

---

## 6. 四个提交

| commit | 内容 | 为什么单独一个 |
|---|---|---|
| `2601bab` | 改名补完 Makefile / CI / ruff / verify 提示 | 让新人的前三条命令能跑 |
| `e638310` | VIX 下限融合 + 两个研究脚本 | 唯一通过测试的融合 |
| `1e2c679` | 收窄 VIX floor 的异常捕获 | 修行为而非修计数 |
| `d5de826` | VIX 陈旧读数拒绝 | 计划预警的真缺陷 |

`docs/history/plans/2026-10-09-project-review-and-roadmap.md` 本身未动。

---

## 7. 我不做的，以及为什么

**实盘。** 计划 §11 明确"本轮不执行实盘"，AGENTS.md §17 禁止，而证据不支持：6 个策略里 5 个跑输
买入持有，唯一存活的判断是风险而非方向，第一次可演化修订（波动率目标 12%→8%，Sharpe +0.0138）
**以收益下降为代价且未应用**。

**不为让门禁变绿而动风控。** 本轮两次门禁变红，两次都改了代码而不是改门禁。

---

## 8. 下一步（计划自己的阶段顺序）

1. **阶段 B 可信测量**：先定 benchmark 的计算合同（资金口径、窗口、成本、总体判决），再谈页面。
   这是当前最高杠杆的一步——现在"5/6 落后"和我修的那个"分母不同源"是同一类问题。
2. **阶段 A 收尾**：`research` 可选依赖 + make 入口，让新人能重跑一个真实数据研究。
3. **VIX 下限的 A/B**：现在它已上线，但它对回撤/Sharpe 的真实影响未在同口径下与"无下限"对比。
4. **60 个交易日**：需要 12 个交易周，不由开发日历决定。当前 19/60。
