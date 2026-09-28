#!/usr/bin/env python
"""自动审查与修复系统（统一入口；auto-review.sh 已废弃）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/localscratch/liuyime2/QuantGroup")
REVIEW_DIR = ROOT / "reviews"
STRATEGY_DIR = ROOT / "runtime/min_agent/strategies"
KNOWLEDGE_DIR = ROOT / "runtime/min_agent/knowledge"

REVIEW_DIR.mkdir(exist_ok=True)

# `conda` is not on PATH in this environment; ~/.bashrc is a single newline.
# Every script used to invoke bare `conda`, so a missing interpreter produced
# `command not found` on stderr, which `| tee` swallowed, and the script still
# exited 0 and printed a green banner.
CONDA_BIN = os.environ.get("CONDA_BIN", "/home/liuyime2/miniconda3/bin/conda")
CLI_TIMEOUT_SECONDS = 180
TEST_TIMEOUT_SECONDS = 900
REVIEW_RETENTION_DAYS = 7

CORE_STRATEGIES = (
    "fixed-size-buy-001",
    "fixed-size-sell-001",
    "trend-follow-buy-001",
    "trend-follow-sell-001",
)

# Pin paper-only env once instead of re-copying os.environ for every CLI call.
# Use unconditional assignment (not setdefault) so a stale `/v2`-suffixed
# ALPACA_BASE_URL inherited from the user's shell can't double-suffix downstream
# clients.
os.environ["PYTHONPATH"] = "src"
os.environ["ALPACA_BASE_URL"] = "https://paper-api.alpaca.markets"
os.environ["APCA_API_BASE_URL"] = "https://paper-api.alpaca.markets"


def cli_json(*args: str) -> tuple[dict | None, int | None]:
    """Run a min_agent.cli flag.

    Returns (parsed stdout, exit code). The exit code is returned even when the
    payload parses, because `--status` now exits non-zero when the daemon is
    dead; treating that as "no output" is what let a 97-day-dead daemon pass 112
    consecutive reviews.
    """
    if not os.access(CONDA_BIN, os.X_OK):
        return None, None
    cmd = [CONDA_BIN, "run", "-n", "llm", "python", "-m", "min_agent.cli", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return None, None
    try:
        return json.loads(result.stdout), result.returncode
    except json.JSONDecodeError:
        return None, result.returncode


def prune_old_reviews() -> int:
    """AUTO_REVIEW_README.md promises 7-day retention; nothing implemented it."""
    cutoff = datetime.now().timestamp() - REVIEW_RETENTION_DAYS * 24 * 3600
    removed = 0
    for path in REVIEW_DIR.glob("review-*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def main() -> int:
    now = datetime.now()
    print("=" * 60)
    print("🔍 Quant Voyager 自动审查系统")
    print("=" * 60)
    print(f"时间: {now}")
    print()

    issues: list[dict] = []

    # 0. 环境 / 解释器
    print("[0/8] 检查解释器与外部依赖...")
    if not os.access(CONDA_BIN, os.X_OK):
        print(f"  ❌ conda 不可用: {CONDA_BIN}")
        issues.append({"type": "conda_missing", "severity": "high", "path": CONDA_BIN})
    else:
        print(f"  ok    {CONDA_BIN}")
    env_check, env_rc = cli_json("--check-env")
    if env_check is None:
        print("  ❌ --check-env 无输出 (超时或解释器失败)")
        issues.append({"type": "check_env_unavailable", "severity": "high"})
    else:
        missing = env_check.get("missing_alpaca_credentials") or []
        if missing:
            print(f"  ❌ 缺少 Alpaca 凭据: {missing}")
            issues.append({"type": "missing_alpaca_credentials", "severity": "high", "missing": missing})
        if not env_check.get("ollama_tags_accessible"):
            print("  ❌ Ollama 不可达")
            issues.append({"type": "ollama_unreachable", "severity": "medium"})
        elif not env_check.get("ollama_model_present"):
            print(f"  ❌ 模型缺失: {env_check.get('model')}")
            issues.append({"type": "ollama_model_missing", "severity": "medium", "model": env_check.get("model")})
        else:
            print("  ok    凭据与 Ollama 均就绪")

    # 1. 守护进程 — 必须看 liveness，不能只看 status 字符串
    print("\n[1/8] 检查守护进程 (liveness)...")
    status, status_rc = cli_json("--status")
    if status is None:
        print("  ❌ --status 无输出")
        issues.append({"type": "status_unavailable", "severity": "high"})
    else:
        print(f"  状态: {status.get('status')}  live={status.get('live')}")
        print(f"  pid={status.get('pid')} pid_alive={status.get('pid_alive')} "
              f"age_seconds={status.get('age_seconds')} stale={status.get('stale')}")
        # A frozen heartbeat with a dead pid reads "RUNNING" forever. Judge liveness.
        if not status.get("live"):
            issues.append({
                "type": "daemon_not_live",
                "severity": "high",
                "status": status.get("status"),
                "pid_alive": status.get("pid_alive"),
                "stale": status.get("stale"),
                "age_seconds": status.get("age_seconds"),
            })
        if status_rc not in (0, None):
            issues.append({"type": "status_exit_nonzero", "severity": "high", "exit_code": status_rc})
        error_count = status.get("error_count", 0)
        if error_count > 10:
            issues.append({"type": "high_errors", "severity": "high", "count": error_count})

    # 2. 经纪人对账
    print("\n[2/8] 检查经纪人对账...")
    reconcile, _ = cli_json("--reconcile")
    reconcile = reconcile or {}
    matched = bool(reconcile.get("matched", False))
    print(f"  对账: {'✅' if matched else '❌'}")
    if not matched:
        issues.append({"type": "reconcile_mismatch", "severity": "high"})

    # 3. 策略库 — 只读
    print("\n[3/8] 检查策略库 (只读)...")
    specs: dict[str, dict] = {}
    missing: list[str] = []
    for name in CORE_STRATEGIES:
        p = STRATEGY_DIR / f"{name}.json"
        if not p.exists():
            missing.append(name)
            continue
        try:
            specs[name] = json.loads(p.read_text())
        except json.JSONDecodeError as exc:
            print(f"  ⚠️ 无法解析 {name}: {exc}")
            issues.append({"type": "unparseable_strategy", "severity": "high", "strategy": name})
    selectable = sum(
        1 for d in specs.values()
        if d.get("enabled") and d.get("lifecycle") not in {"PAUSED", "RETIRED"}
    )
    print(f"  可选策略: {selectable}/{len(CORE_STRATEGIES)}")
    if missing:
        issues.append({"type": "missing_core_strategies", "severity": "high", "strategies": missing})
    if selectable < 2:
        issues.append({"type": "too_few_selectable_strategies", "severity": "medium", "selectable": selectable})

    # 4. 知识库
    print("\n[4/8] 检查知识库...")
    lessons = len(list(KNOWLEDGE_DIR.glob("*.json"))) if KNOWLEDGE_DIR.exists() else 0
    print(f"  知识工件: {lessons}")

    # 5. 证据
    print("\n[5/8] 检查 PnL 证据...")
    evidence, _ = cli_json("--evidence-report")
    evidence = evidence or {}
    submitted = evidence.get("submitted_orders", 0)
    pnl_status = (evidence.get("pnl") or {}).get("status", "unknown")
    print(f"  提交订单: {submitted}")
    print(f"  PnL 证据: {pnl_status}")

    # 6. 测试
    print("\n[6/8] 运行测试...")
    if not os.access(CONDA_BIN, os.X_OK):
        test_rc = 127
    else:
        try:
            test_rc = subprocess.call(
                [CONDA_BIN, "run", "-n", "llm", "python", "-m", "pytest", "tests/min_agent", "-q"],
                timeout=TEST_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            test_rc = 124
    tests_ok = test_rc == 0
    print(f"  测试: {'✅' if tests_ok else f'❌ (rc={test_rc})'}")
    if not tests_ok:
        issues.append({"type": "tests_failing", "severity": "high", "exit_code": test_rc})

    # 7. 汇总 — 不做任何自动修复
    print("\n[7/8] 问题汇总 (不自动修复)...")
    if not issues:
        print("✅ 未发现问题")
    else:
        print(f"⚠️ 发现 {len(issues)} 个问题:")
        for n, issue in enumerate(issues, 1):
            print(f"  {n}. {issue['type']} (severity: {issue.get('severity')})")
        print()
        print("本审查器不做任何自动修复，这是设计决定 (AGENTS.md 6):")
        print("  - 提升风险上限会削弱硬风控")
        print("  - 覆写 lifecycle 会覆盖基于真实证据的生命周期决策")
        print("  - 两者都会绕过 StrategyAdmission 与 Guardian.review_strategy")
        print("请通过 curriculum -> StrategyAdmission 路径变更，并使其被 journal 记录。")

    # 8. 保留期
    print("\n[8/8] 清理过期审查报告...")
    removed = prune_old_reviews()
    print(f"  已删除 {removed} 份超过 {REVIEW_RETENTION_DAYS} 天的报告")

    report_file = REVIEW_DIR / f"review-{now.strftime('%Y%m%d-%H%M%S')}.json"
    report_file.write_text(json.dumps({
        "timestamp": now.isoformat(),
        "ok": not issues,
        "issues": issues,
        "tests_ok": tests_ok,
    }, indent=2))

    print(f"\n报告已保存: {report_file}")
    print("=" * 60)
    print("✅ 审查完成")
    print("=" * 60)
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
