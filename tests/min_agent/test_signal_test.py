"""The signal gate: powered, block-resampled, and counting every hypothesis ever tried.

All data here is SYNTHETIC and says so: these tests prove the gate does what it claims, not
anything about markets. The market findings live in docs/evidence/signal-research-2026-10-08/.
"""

from __future__ import annotations

import random

from min_agent.research import signal_test as st


def _blocks_xy(days=60, per_day=20, slope=0.0, seed=1):
    rng = random.Random(seed)
    blocks, x, y = [], [], []
    for d in range(days):
        for _ in range(per_day):
            xv = rng.gauss(0, 1)
            blocks.append(d)
            x.append(xv)
            y.append(slope * xv + rng.gauss(0, 1))
    return blocks, x, y


def test_a_planted_signal_is_found_and_noise_is_not(tmp_path):
    b, x, y = _blocks_xy(slope=0.5)
    found = st.block_rank_correlation(b, x, y, reps=400)
    assert found.rho > 0.3 and found.low > 0 and found.p < 0.01
    b, x, y = _blocks_xy(slope=0.0, seed=2)
    noise = st.block_rank_correlation(b, x, y, reps=400)
    assert noise.low < 0 < noise.high and noise.p > 0.05


def test_resampling_blocks_is_wider_than_pretending_every_bar_is_independent():
    """Twenty days, each one value repeated 40 times: forty copies are one observation."""
    rng = random.Random(3)
    blocks, x, y = [], [], []
    for d in range(20):
        xv, yv = rng.gauss(0, 1), rng.gauss(0, 1)
        blocks += [d] * 40
        x += [xv] * 40
        y += [yv] * 40
    clustered = st.block_rank_correlation(blocks, x, y, reps=400)
    unclustered = st.block_rank_correlation(list(range(len(x))), x, y, reps=400)
    assert (clustered.high - clustered.low) > 3 * (unclustered.high - unclustered.low)


def test_too_little_data_is_underpowered_not_a_number_that_looks_like_evidence(tmp_path):
    res = st.block_rank_correlation([0, 0, 1, 1], [1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
    assert res.rho != res.rho and res.p != res.p
    tr = st.CorrelationResult(100, 20, 0.1, 0.0, 0.2, 0.01)
    assert st.judge("x", tr, res, ledger=tmp_path / "l.jsonl")["verdict"] == "UNDERPOWERED"


def test_the_bar_rises_with_every_hypothesis_the_ledger_has_seen_and_failures_are_recorded(tmp_path):
    led = tmp_path / "signals.jsonl"
    sel = st.CorrelationResult(500, 40, 0.05, 0.0, 0.1, 0.04)
    weak = st.CorrelationResult(500, 40, 0.04, -0.01, 0.09, 0.03)   # significant alone, not against a family of many
    bars = []
    for i in range(12):
        row = st.judge(f"h{i}", sel, weak, ledger=led)
        bars.append(row["bar"])
        assert row["verdict"] == ("SURVIVES" if row["bar"] > 0.03 else "NOT_SIGNIFICANT")
    assert bars[0] > bars[-1] and abs(bars[0] - 0.05) < 1e-12 and abs(bars[-1] - 0.05 / 12) < 1e-12
    assert st.count_trials(led) == 12 and all(r["kind"] == "signal" for r in st.read_ledger(led))


def test_a_signal_that_changes_sign_between_selection_and_test_does_not_survive(tmp_path):
    sel = st.CorrelationResult(500, 40, 0.20, 0.1, 0.3, 0.001)
    flipped = st.CorrelationResult(500, 40, -0.20, -0.3, -0.1, 0.0005)
    assert st.judge("flip", sel, flipped, ledger=tmp_path / "l.jsonl")["verdict"] == "SIGN_FLIPPED"
    same = st.CorrelationResult(500, 40, 0.20, 0.1, 0.3, 0.0005)
    assert st.judge("same", sel, same, ledger=tmp_path / "l.jsonl")["verdict"] == "SURVIVES"


def test_time_split_reads_the_later_days_once():
    days = [f"2024-{m:02d}-{d:02d}" for m in range(1, 11) for d in range(1, 11)]
    cut = st.time_split(days, 0.6)
    assert cut == sorted(days)[60] and sum(1 for d in days if d >= cut) == 40


def test_a_signal_that_does_not_hold_once_its_common_driver_is_removed_is_confounded(tmp_path):
    sel = st.CorrelationResult(900, 40, 0.03, 0.0, 0.06, 0.1)
    test = st.CorrelationResult(900, 40, 0.09, 0.04, 0.14, 0.0003)           # clears the bar on its own
    vanished = st.CorrelationResult(900, 40, 0.02, -0.03, 0.06, 0.37)         # beta times the market taken out
    row = st.judge("vol->ret", sel, test, control=vanished, ledger=tmp_path / "l.jsonl")
    assert row["verdict"] == "CONFOUNDED" and row["control"]["rho"] == 0.02
    held = st.CorrelationResult(900, 40, 0.08, 0.03, 0.13, 0.0002)
    assert st.judge("vol->ret2", sel, test, control=held, ledger=tmp_path / "l.jsonl")["verdict"] == "SURVIVES"
