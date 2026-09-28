#!/bin/bash
# 监控脚本 - 运行完整的监控序列
set -e

cd /localscratch/liuyime2/QuantGroup

echo "========================================"
echo "📊 Quant Voyager 监控运行"
echo "========================================"
date

echo ""
echo "[1/5] 检查 Daemon 状态..."
PYTHONPATH=src ALPACA_BASE_URL=https://paper-api.alpaca.markets conda run -n llm python -m min_agent.cli --status 2>&1 || echo "Daemon 检查完成"

echo ""
echo "[2/5] Broker 同步检查..."
PYTHONPATH=src ALPACA_BASE_URL=https://paper-api.alpaca.markets conda run -n llm python -m min_agent.cli --reconcile 2>&1 || echo "同步检查完成"

echo ""
echo "[3/5] 更新 Broker 证据..."
PYTHONPATH=src ALPACA_BASE_URL=https://paper-api.alpaca.markets conda run -n llm python -m min_agent.cli --ingest-evidence 2>&1 || echo "证据更新完成"

echo ""
echo "[4/5] 生成证据报告..."
PYTHONPATH=src ALPACA_BASE_URL=https://paper-api.alpaca.markets conda run -n llm python -m min_agent.cli --evidence-report 2>&1 || echo "报告生成完成"

echo ""
echo "[5/5] 详细分析..."
PYTHONPATH=src conda run -n llm python monitor.py 2>&1 || echo "分析完成"

echo ""
echo "========================================"
echo "✅ 监控完成"
echo "========================================"
