# Global Working Principles

## 1. Documentation First

Any code modification must enter Plan Mode first. Do not make direct code changes
without a written plan.

## 2. Real Data Only

Do not use mock data, fabricated data, or invented experiment results. Any
experiment, benchmark, backtest, or evaluation result must come from real
execution and must be reproducible.

## 3. Andrej Karpathy Four Principles

- Think Before Coding
- Simplicity First
- Surgical Changes
- Goal-Driven Execution

## 4. Optimization Mechanism First

Problems in Skill scripts must be discovered and fixed through the optimization
mechanism. Do not manually edit Skills to bypass the mechanism.

## 5. Skill Usage Standard

Use the Superpowers skill system by default. `planning-with-files` is the
default gate before implementation work.

## 6. Core Mission: Autonomous Self-Evolving Trading Agent

The project goal is to build a self-evolving autonomous trading agent whose
ultimate success metric is real profitability over time.

The agent should eventually be able to:

- collect real market and account data;
- analyze opportunities with large language models;
- make trading decisions autonomously;
- execute approved trades;
- reflect on results;
- improve its own strategy, memory, prompts, and operating policies.

The minimum viable system must start with paper trading and a complete feedback
loop. Live trading is forbidden until paper-trading behavior is audited and hard
risk controls are verified.

Autonomy is bounded by non-negotiable safety rules:

- no mock or fabricated data in paper/live paths;
- no bypass of Guardian risk checks;
- no bypass of market-open checks;
- no LLM-generated shell execution;
- no automatic weakening of hard risk limits.
