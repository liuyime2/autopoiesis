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


def cli_json(*args: str) -> dict | None:
    """Run a min_agent.cli flag and parse stdout as JSON, or None on failure."""
    cmd = ["conda", "run", "-n", "llm", "python", "-m", "min_agent.cli", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def main() -> int:
    now = datetime.now()
    print("=" * 60)
    print("🔍 Quant Voyager 自动审查系统")
    print("=" * 60)
    print(f"时间: {now}")
    print()

    issues: list[dict] = []

    # 1. 守护进程
    print("[1/7] 检查守护进程...")
    status = cli_json("--status") or {}
    error_count = status.get("error_count", 0)
    daemon_status = status.get("status", "unknown")
    print(f"  状态: {daemon_status}")
    print(f"  错误数: {error_count}")
    if daemon_status != "RUNNING":
        issues.append({"type": "daemon_stopped", "severity": "high", "status": daemon_status})
    if error_count > 10:
        issues.append({"type": "high_errors", "severity": "high", "count": error_count})

    # 2. 经纪人对账
    print("\n[2/7] 检查经纪人对账...")
    reconcile = cli_json("--reconcile") or {}
    matched = bool(reconcile.get("matched", False))
    print(f"  对账: {'✅' if matched else '❌'}")
    if not matched:
        issues.append({"type": "reconcile_mismatch", "severity": "high"})

    # 3. 策略库 — 一次性加载并缓存，修复阶段直接复用
    print("\n[3/7] 检查策略库...")
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

    enabled = sum(1 for d in specs.values() if d.get("enabled", False))
    disabled = len(specs) - enabled
    print(f"  启用策略: {enabled}")
    print(f"  禁用策略: {disabled}")
    if missing:
        issues.append({"type": "missing_core_strategies", "severity": "high", "strategies": missing})
    if enabled < 2:
        issues.append({"type": "too_few_enabled", "severity": "medium", "enabled": enabled})

    # 4. 知识库
    print("\n[4/7] 检查知识库...")
    lessons = len(list(KNOWLEDGE_DIR.glob("*.json"))) if KNOWLEDGE_DIR.exists() else 0
    print(f"  知识工件: {lessons}")

    # 5. 证据
    print("\n[5/7] 检查 PnL 证据...")
    evidence = cli_json("--evidence-report") or {}
    submitted = evidence.get("submitted_orders", 0)
    pnl_status = evidence.get("pnl", {}).get("status", "unknown")
    print(f"  提交订单: {submitted}")
    print(f"  PnL 证据: {pnl_status}")

    # 6. 测试
    print("\n[6/7] 运行测试...")
    test_rc = subprocess.call(
        ["conda", "run", "-n", "llm", "python", "-m", "pytest", "tests/min_agent", "-q"]
    )
    tests_ok = test_rc == 0

    # 7. 修复 — 复用步骤 3 解析过的 spec dict，避免再次开文件
    print("\n[7/7] 问题检测与修复...")
    if not issues:
        print("✅ 未发现问题")
    else:
        print(f"⚠️ 发现 {len(issues)} 个问题:")
        for n, issue in enumerate(issues, 1):
            print(f"  {n}. {issue['type']} (severity: {issue.get('severity')})")
        if any(i["type"] == "too_few_enabled" for i in issues):
            print("\n🔧 启用核心策略...")
            for name, data in specs.items():
                if data.get("enabled", False):
                    continue
                data["enabled"] = True
                data["lifecycle"] = "PROBATION"
                (STRATEGY_DIR / f"{name}.json").write_text(json.dumps(data, indent=2))
                print(f"  ✅ 已启用: {name}")

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
