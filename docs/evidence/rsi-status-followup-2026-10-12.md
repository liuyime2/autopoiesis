# 2026-10-12 夜间状态复核

审查提交 `49063c8`，当地日期 America/Detroit 2026-10-12。审查开始时 `src/autopoiesis/research/prereg.py` 已有未提交修改；本次不修改或提交该文件。只审查并记录，不下单、不重启、不调整风险限额。

## 相对上一份审查的变化

- `984e791` 已使用 tape provenance，修掉 RUF059，并将默认 provenance 输出测试改为明确的 synthetic heartbeat；真实主机检查在无 heartbeat 环境跳过。
- `0794674` 修了 requests typed superclass 的 override/参数转发问题；该提交的远端三版本 CI 曾成功。
- `f80e137` 增加策略覆盖标的不变量门禁，并记录历史污染；这不是重新验证被污染策略的通过证据。
- `49063c8` 修复 provenance 读取 `dataset_fingerprint`。旧 99 行没有指纹的事实没有改变。
- preregistry 路径正在工作树里修改，尚未提交；不能按已部署验收处理。还需非 editable 安装、独立配置路径、新克隆和实际入口验证。

## 最新 CI 的真实失败与复现

[CI run 38336267347](https://github.com/liuyime2/autopoiesis/actions/runs/38336267347) 对应 `49063c8`。Python 3.10/3.11/3.12 均在 unit and integration tests 失败，lint/type 阶段已过。release 成功，Pages 单独成功，但不等于行为门禁通过。

读取 Python 3.12 job `115096943374` 日志：1 failed、1323 passed、5 skipped。失败为 `test_a_calm_index_leaves_the_forecast_alone`：固定 synthetic VIX 数据最新日期为 2026-10-08，UTC runner 执行时已是 10-13，返回 `5 days old; floor not engaged`，测试却要求 `below the forecast`。

在独立克隆 `/tmp/autopoiesis-review-aDTZYL/tree` 实际复现：

```sh
TZ=UTC make test TEST_ARGS='tests/autopoiesis/test_risk_judgment.py::test_a_calm_index_leaves_the_forecast_alone -q'
```

结果相同，1 FAILED。该克隆中 runtime-integrity/provenance 两个测试文件通过，真实主机项明确 skip。下一修复是固定测试时钟并覆盖 fresh/stale/future 三种合同；不是放宽生产行情过期保护。

本机工作树（含审查开始时已有的 prereg.py 未提交修改）`make check` 实际通过：lint、mypy（48 个 source files）、1329 passed，315.80s。独立 UTC 克隆测试的是已提交 `49063c8`。本机结果与 UTC runner 的差异必须保留，不能用本机绿色覆盖远端红色。

## 运行、研究和盈利

daemon active，最新启动 18:26 EDT；收盘后 heartbeat 为 maintenance only，broker 正常。cycle_count=0、model_ok=null 是该进程启动后尚无市场周期，不表示开盘运行验证完成。交易最后周期仍为 16:00；没有修复后新的开盘成交证据。

research ledger 仍为 143 行：88 INSUFFICIENT、44 OVERFIT、11 INCONCLUSIVE_NO_FOLDS、0 PASS、99 行缺 dataset_fingerprint；最近搜索仍是今早 134456 根十年合并数据。Makefile 与定时任务仍不强制 spec/verify/check-tape。服务 success 不表示遵循实验合同。

最新已复核的收盘报告仍为 266 cycles、0 cycle errors、0 submitted orders；诊断净 paper PnL +811.72、agent +1.79% vs SPY +3.32%、20/60 有记录交易日。此轮未重跑同一 benchmark，以上引用上一轮真实输出；不存在新收益证据。资本分母和连续观察窗口仍不满足计划。

演化提案/反思/生命周期机制在运行，但没有受控 research episode control/treatment 证据。历史 audit 新量化 862/1602 有 strategy_id 的 cycles 错配，涉及 7 策略；污染集中于 10-08、10-09、10-12 三个交易日。相关评分和生命周期必须基于干净样本重新评审。

历史审查文字也有待核对：`fixed-size-probe-0001` 的 102/180 错配不能同时解释为“从未在 SPY 上运行”。必须区分整个累计样本与具体判决窗口，不能用修辞扩大污染结论或手工反转生命周期。

## 下一步的明确顺序

1. 固定测试时钟，完成最新提交的三版本 CI；保持隔离环境证明，核对依赖版本差异。
2. 在下一市场会话验证 symbol 不变量、有效周期和 probation 公平分配；将受污染 cohort 从有效评分/准入依据中隔离，保留原日志。
3. 让研究的 spec 注册验证、数据质量、symbol/interval/window、候选/预算/成本一致性成为唯一入口的必经步骤；修 preregistry、dirty 判定和 provenance 的完整合同。
4. 统一同步组合权益、资金占用、成本和 owner/agent 分离，补齐 outcome/calibration；然后比较规则与 LLM 的等预算净增量。
5. 固定 research control，对有来源的失败 lesson 和后续假设选择做 treatment；全部 episode 留档，以预注册效率指标评估 RSI，独立 holdout 评估 alpha。
6. 持续异地备份、恢复演练、可取消阶段和阶段计时；减少生产 journal 在默认单测/报告中的重复全量计算。
7. 独立 holdout → shadow → 有期限单候选 paper probation → 连续完整 60 日验证。保持 paper-only 和 Guardian 硬限额。

结论：修复在推进，演化机制已运行；最新 CI 尚未通过，可信 research alpha 仍为 0，受控 RSI 增益和可靠盈利尚未证明。
