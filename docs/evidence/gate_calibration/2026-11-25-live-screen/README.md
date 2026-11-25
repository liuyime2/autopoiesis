# 在线筛选器的 E1/E2(2026-11-25)

对象:线上版本 `02a3d5e` 的 `offline_validation.validate`,即 RSI 环里决定暂停、淘汰和放行加仓的那道筛选。
11-20 起的校准只覆盖研究闸门(v4、横截面、v5/v6),没有覆盖它。

## 缺陷

`_screen` 里 `elif record.verdict == GOOD_TRADE and not paired`(提交 `41886bb`,11-05,步骤 1.5):
配对决策(规则动作的评分)里**正确的交易**不计入 `good_trades`,因此不计入 `correct_outcomes`,
但仍计入分母 `scored`。意图是"不当作成交证据",结果是"当作做错了"。
`paired_check.py`(合成记录):60 笔全部正确,不配对 → PASS;配对 → REJECT,"0 of 60 right"。

## 对线上记录的影响(`live_impact.py`,只读,输出见 `live_impact.txt`)

2,842 个配对周期里 877 个正确的规则交易被算作错误。119 个有记录的策略里,当前判 REJECT 的有 30 个,
**其中 24 个在计入正确交易后不再是 REJECT**(全部变为 INCONCLUSIVE,没有一个变为 PASS);6 个仍然 REJECT。
这 24 个里,生命周期理由引用了筛选结论的有 6 个,其余 18 个以别的理由暂停或淘汰。
daemon 每次运行都在追加日志,重跑的计数会随之变化;本数字是 2026-11-25 盘中读取的。

## E1 / E2(`screen_spec.md` 先写,`screen_cal.py`,每格 1000 次,两次运行逐字节一致)

策略是**合成**的,市场结果是日志里记录的真实结果。只有 SPY 满足"至少 20 个可用日",结论只覆盖它。

| 策略 | 现行,不配对 PASS / REJECT | 现行,配对 PASS / REJECT | 计入修正,配对 PASS / REJECT |
|---|---|---|---|
| 无技能,逐周期 | 1.2% / 5.9% | 0% / 100% | 1.2% / 5.9% |
| 无技能,按日固定 | 1.6% / 6.3% | 0% / 100% | 1.6% / 6.3% |
| 无技能,含 HOLD | 2.6% / 5.8% | 0% / 100% | 2.6% / 5.8% |
| 技能 q=0.25 | 10.2% / 0.9% | 0% / 100% | 10.2% / 0.9% |
| 技能 q=0.5 | 31.4% / 0.2% | 0% / 100% | 31.4% / 0.2% |
| 技能 q=1.0 | 87.1% / 0% | **0% / 100%** | 5.2% / 0% |

(合成策略只产生 BUY/SELL 或 HOLD;"计入修正 + 不配对"与"现行 + 不配对"相同。)

## 判定

1. **现行筛选器对配对记录没有任何区分能力**:无技能和完美技能都是 100% 拒绝。E1 的 PASS 率为 0 只是因为什么都过不了。
2. **第二个缺陷**:即使计入正确交易,PASS 还要求至少 5 笔"成交证据"(`good_trades + false_trades`),
   配对的正确交易不在其中,于是配对策略要靠亏损交易凑数,完美策略只有 5.2% 通过。
3. **不配对时 E2 也不通过**:q=0.5 的找回率 31.4%,判据是 ≥80%。
4. 不配对的无技能 REJECT 率约 6%,高于单侧 2 个标准误的名义约 2.3%;可能是日内相关让标准误偏小,未单独验证。
5. 修正第 1 条不会让任何现有策略通过,也不构成盈利证据。

## 没做

本节以上写于修复之前。修复见下;没有部署,已被误判的策略如何处理由账户持有人决定。

## 研究闸门的独立复现

在提交 `55ea947` 的全新检出上重跑 `tools/run_gate_calibration.py`(默认 200 次零数据、每档 100 次,8 进程,666 s):
数据指纹、种子、汇总和逐次结果与 `2026-11-20.json` **全部相同**。

## 复现

在本目录运行;`PYTHONPATH` 指向 `02a3d5e` 检出的 `src`,`AUTOPOIESIS_RUNTIME` 指向有日志和策略库的运行目录。
日志是线上状态,不在仓库里;`screen_cal.py` 与 `after_fix.py` 只读 2026-11-25 之前的行,数据指纹写在 JSON 里。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$DEPLOYED_SRC python screen_cal.py /tmp/screen_cal.json
```

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$DEPLOYED_SRC python after_fix.py $FIXED_SRC/autopoiesis/offline_validation.py /tmp/after_fix.json
```

## 修复后(分支 `fix/screen-paired-trades`,`after_fix.py`,每格 4000 次,两次运行逐字节一致)

改动:正确的配对交易计入 `shadow_good_trades` 并算作正确;PASS 的交易证据包含它;REJECT 用
`max(se, se_clustered)`。

| 配对记录,PASS / REJECT | 现行 `02a3d5e` | 修复后 |
|---|---|---|
| null_cycle | 0.0% / 100.0% | 1.6% / 3.4% |
| null_day | 0.0% / 100.0% | 1.8% / 3.0% |
| null_hold | 0.0% / 100.0% | 2.6% / 2.5% |
| skill0.25 | 0.0% / 100.0% | 9.7% / 0.3% |
| skill0.5 | 0.0% / 100.0% | 31.7% / 0.0% |
| skill1.0 | 0.0% / 100.0% | 88.0% / 0.0% |

| 判据 | 结果 |
|---|---|
| 配对与不配对结论一致 | 24,000 组,0 处不一致 |
| 无技能 PASS 的 Wilson 上界 ≤5% | 通过(≤3.2%) |
| 无技能 REJECT ≤4% | 通过(2.5–3.4%) |
| 技能 q≥0.25 的 REJECT 低于无技能;完美技能 PASS ≥80% | 通过(≤0.3%;88.0%) |
| q=0.5 找回率 ≥80% | **不通过(31.7%)**,计划中已预期;20 天样本的限制,本次不解决 |

线上只读重算(截至 2026-11-25 之前的行):30 个 REJECT 里 **26 个变为 INCONCLUSIVE,4 个仍 REJECT,没有任何策略变为 PASS**。
其中生命周期理由引用筛选结论的 5 个:

| 策略 | 状态 |
|---|---|
| `fixed-size-buy-spy-001` | RETIRED |
| `fixed-size-buy-tlt-001` | RETIRED |
| `trend-follow-20260808-001` | RETIRED |
| `trend-follow-20260826-001` | RETIRED |
| `trend-follow-tlt-001` | RETIRED |
