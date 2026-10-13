# RSI 审查修复与验证记录（2026-10-13）

本轮目标是修复已识别的证据、计算与可靠性缺陷，使研究和学习在证据不足时拒绝给出成功结论。它不是“完成盈利或证明 RSI”的交付。代码在 `codex/rsi-audit-repairs`，供审查的 PR 为 https://github.com/liuyime2/autopoiesis/pull/10 。主工作树既有的 prereg.py 未提交修改保留；交易 daemon 尚未切换到此分支。

## 已修复的核心路径

| 部分 | 缺陷及修复 | 实际证据与边界 |
|---|---|---|
| 可复现检查 | VIX 测试依赖当天日期/UTC；provenance 混入主机 heartbeat、Git 签名配置。固定合成测试时钟，隔离外部配置，真实 Git 状态包含未跟踪文件及失败状态 | UTC/Detroit 专项检查均通过；生产四天行情新鲜度约束没有放宽 |
| 研究合同 | Make/定时入口可绕过声明；十年合并数据与六个月声明不一致；缓存身份、重复试验及落盘失败未严格约束 | 强制注册合同、标的/时间/区间/成本/预算/来源校验；冲突 bars、非法结果和台账写入失败拒绝运行；安装后的 registry 位于工作目录 |
| 回测计算 | 入场费用漏算、回撤只看端点、按开仓基准重复复利、因果前缀反复复制 | 合成的 100→120→90→110 路径回撤修正为 25%；平价完整交易计入双边费用；前缀视图避免重复切片。合成测试只证明计算逻辑 |
| 学习证据 | 用当前可变策略解释过去决策；UNKNOWN、旧行为、受污染订单别名和缓存可能供给学习/晋升 | 冻结决策时策略行为和 universe；所有学习消费者检查身份与来源；永久保留已知 client/broker 别名污染；旧账本和 FIFO 核算保留，缺证据的样本不参与学习 |
| 收益披露 | 独立策略峰值资金之和被当作 agent 资本；短期账户数据被当作组合达标 | 公共/操作面板、benchmark、CLI 和 daemon 统一 UNKNOWN；资本/收益率/超额为 null，账户数据仅作诊断。`make evaluate` 接受未知测量但拒绝执行异常/不一致 JSON；保留有归属的纸上核算及明确标注的峰值诊断 |
| 本地备份 | 发现文件与复制未共享一致性边界；深层 generation 遗漏；fsync/归档清单及重试可能虚假认证 | 锁内发现/复制，密封副本校验，所有数字 generation 按历史顺序读取/归档；无效/未认证清单阻止完成备份。真实恢复和演练通过，小时备份任务已启用 |
| 停止流程 | 信号处理器做 IO；停止后仍开新阶段；回调异常重复计数或丢失完成 ID | 信号只置标志；启动、维护、clock/backoff、screen/post-cycle 等边界检查；每次尝试计数一次，回调前记录完成 ID。64 个专项测试通过；这不证明生产停机耗时 |

每个非平凡修改有书面问题、测量基线、验收标准、合成红色回归及独立规格/质量复审。提交和具体命令见 `docs/superpowers/plans/2026-10-12-audit-repairs.md` 及各子计划。

## 真实执行结果

- **研究诊断**：选定一份真实 SPY 5Min cache；声明窗口内 24,111 bars，数据 SHA-256 为 `e3c2bf54eb495e8ccd82502485d7aaf7a239e2838c0d1ba8d83984e4acd75ea4`。最终 evaluator 为 `4501e3a03f4895726b38a5e699475666342b7f70bd09ad3e0b7332199e44e6ea`。11 次新回测得到 4 OVERFIT、7 INSUFFICIENT、0 PASS；隔离诊断 ledger 保留前一 evaluator 的 11 次记录，共 22 次累计尝试，生产历史 ledger 未重置。首次 52.96s，完全重复 6.22s、0 新回测。注册发生于历史窗口之后，因此是合同/完整性诊断，不能当作科学预注册、独立 holdout 或 alpha。
- **历史学习审计**：2,326 cycles 全部缺少新的冻结决策时 provenance，归为 UNKNOWN；可用于新策略学习的组为 0。1,602 个有策略归属的历史周期及原始核算保留。实际 decision/snapshot 标的冲突为 0。此前 862/1602 计数比较的是历史标的与“今天的策略 universe”，不构成冻结身份冲突证据；不能补造历史 provenance 来恢复资格。
- **纸上核算**：密封恢复的 journal 有 realized 757.95、unrealized 89.59、assumed cost 35.8189，净核算 811.72。observed fees=0 只表示未观察到费用。记录覆盖 20 个不同日期，不能视为连续 20 个有效交易日，更不满足完整 60 日窗口。资本、同步权益和现金流归属缺失，组合收益率/同期持有基准/超额均不可测量。
- **本地恢复**：5 文件、219,034,423 bytes，2,326 cycles、21,671 events、4 journal generations、0 未解析行、CERTIFIED archive；隔离恢复逐字节一致，47 个 closed lots 及账本金额一致，完整 drill PASS。小时服务实际执行 exit 0/Result=success，timer enabled/active；没有替换在线 journal。
- **历史公开信号台账**：173 个 verdict rows、129 个 hypotheses；它与新的 threshold-driver 台账分开，不能用 SURVIVES 标签替代当前研究准入。公共输出只发布聚合值，未发布原始 journal、账户持仓、凭据或私有主机路径。

## 集成验证

最终修复源码为 `cd2874e1e0de90606c25443dab0d78cdb22c48e0`；以下是实际执行，后续提交只补本轮真实日志、聚合页面和计划记录。最终证据提交的 CI 以 PR 最新检查页面及交付说明为准；本文件的实验和克隆日志绑定上述源码 SHA。

- `make check ENVFILE=/dev/null` 在 target-consumer 后续修复前：1,484 passed / 6 skipped，36.18s；lint clean，mypy 49 个源文件 clean。
- 最终 target-consumer 修复 `cd2874e`：`make check ENVFILE=/dev/null` 为 **1,499 passed / 6 skipped，38.25s**；lint 和 49 文件类型检查通过。独立规格复审 87 项、质量复审 82 项通过。正/负/缺失账户数据不能标记 agent 达标，且 CLI 元数据使用配置目标、帮助文字明确百分数单位。
- 独立非 editable `make release-check ENVFILE=/dev/null`：exit 0；确认为 venv site-packages 导入，空环境中运行注册/核验，并确认独立工作目录中的 .spec/.json。
- `make verify-self-test ENVFILE=/dev/null`：通过，能检测失败测试、缺名称、空类、未分类文件、过时计划和不可达 systemd 单元。
- 初次默认 Python 3.12 创建 venv 因 host 缺少 ensurepip 失败，脚本记录 FAIL；README 已要求 Debian/Ubuntu 安装 python3-venv。随后显式 Python 3.10 创建新空 venv。首次根目录全 gate 中，工具文件文档漏项已修正；另两次验证读取到并行自检临时文件或尚未写完的 clone log，不算通过。最终验证在日志完成、无临时测试文件时串行执行。
- 最终源码真实全新克隆：`make fresh-clone ENVFILE=/dev/null PYTHON=<explicit Python 3.10>` exit 0，**9 PASS / 0 FAIL**；空配置独立 venv 下 1,498 passed / 7 skipped，55 类门禁全通过、2,488 次测试执行。额外跳过的配置主机检查与空 XDG 配置隔离一致。完整原始输出保留在 [fresh-clone.log](fresh-clone.log)，不是模板填数。
- 最终工作树 `make verify ENVFILE=/dev/null`：**55/55 类、2,489 次测试执行、0 失败**，94 test files。Doctor 的离线 warnings 不等于真实部署通过；公共 collector 收集到 1,505 个测试也不等于它自己执行了这些测试。
- 最终源码 `cd2874e` 的 [GitHub CI](https://github.com/liuyime2/autopoiesis/actions/runs/38348912218) 四个 job（Python 3.10/3.11/3.12 gate 与 release）全部 success。此前 `0fe69e8` 同样通过；不以旧结果代替此次核验。
- 活跃源码、tools/configs/examples、Make/入口、pyproject 和 GitHub workflows 的 `min-agent/min_agent` 引用扫描为 0，已跟踪文件名匹配为 0；历史文档/证据不作为正在运行的旧框架。

最终刷新公共聚合页面成功，仍基于同一密封恢复记录；测试 collection 更新为 1,505 / 94 files，金额和 UNKNOWN 状态不变。首次真正由 timer 触发的本地备份于 01:01:30 EDT 启动、01:02:02 成功完成，生成 `20261013T050130Z-db3c455ce565`，Result=success / ExecMainStatus=0。本轮没有交易服务重启、手动 broker 下单、live 接入或硬风险放宽。

## 当前运行与原计划差距

2026-10-13 04:43 UTC 只读查看：原 daemon active，PID 2800047，heartbeat `RUNNING / market closed; maintenance only`，broker 最近读取成功，model 字段 null，本进程 cycle/error 计数为 0；最后市场周期仍为 2026-10-12 16:00 EDT。源码指纹 `60d5aa8d767ce067` 属于原部署，不能声称这次修复已在市场时段运行。定时研究 service 最近退出成功、timer active，也不能证明它已使用新合同并产生有效研究。

本轮已推进原计划 A/G 的证据隔离、可靠本地恢复和实验闭环，以及 B/C 的合同与判断前提；D 的受控 RSI 对照、E 的新候选 holdout→shadow→paper probation、F 的同步 60 日组合证据仍未完成。运行的启发式提案/演化机制不等于已经证明递归自我改进。

## 下一轮按依赖顺序验收

1. **部署边界**：合入经 CI 验证的修复后更新服务源码/指纹；保留原工作树修改。当前小时备份服务指向保留的修复 worktree，正式部署后更新路径再删除该 worktree。验证下一市场周期的 strategy/symbol/provenance、Guardian、订单归属和停止实测；不通过则保留证据并暂停该候选。
2. **研究运营**：保留冲突 cache 的原始证据，选择可追溯的 canonical dataset；为未来研究事先冻结合同/预算/成本/窗口及 untouched holdout。确认定时任务的真实 last-success、合同/data/evaluator hashes 和所有 verdict。既有历史研究 helpers 仅用于历史复现，未自动认证为当前准入证据。继续报告 null，不降低交易数/风险门槛寻找 PASS。
3. **资金及决策评估**：先定义 agent allocation、owner cash flows、同步权益/成本与基准；从新决策积累冻结 provenance、后续 outcome。相同信息、资金、约束和 horizon 配对比较 deterministic、LLM augmentation、fallback。没有配对样本，不宣称 LLM 知道如何赚钱。
4. **受控 RSI**：冻结 control/treatment、相同预算、预注册 meta-metric 与停止规则；记录每轮研究选择、输入证据、变更原因、计算消耗和所有负结果。按原计划先做工程对照 episodes，独立判断效率/质量增量；不是让系统自己给自己判胜。
5. **进一步可靠性**：在取得真实目标后再做异地备份；补完整状态恢复、存储配额/留存与告警、重启演练、真实 broker 故障/替换链恢复及生产停止耗时验证。当前 journal 恢复不能替代这些验收。

网页浏览器/移动端体验及对外发布验收仍是原计划的后续项；本轮验证的是聚合数据、生成逻辑和脱敏，没有把 HTML 单测替代真实浏览器验收。

不接入 live，不执行 LLM 生成的 shell，不绕过 market-open 或 Guardian，不自动降低硬风险限额。盈利、alpha、LLM 正贡献和 RSI 增量均保持“未证明”，分别以真实证据验收。
