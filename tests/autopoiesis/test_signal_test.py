"""The signal gate: powered, block-resampled, and counting every hypothesis ever tried.

All data here is SYNTHETIC and says so: these tests prove the gate does what it claims, not
anything about markets. The market findings live in docs/evidence/signal-research-2026-10-08/.
"""

from __future__ import annotations

import random

from autopoiesis.research import signal_test as st


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
    # Realistic figures: with 40 resampled blocks, a rho of 0.04 carries a p of about 0.3, not 0.03.
    # An unrealistic p in a fixture is how a gate ends up asserting something no data can produce.
    sel = st.CorrelationResult(500, 40, 0.05, -0.02, 0.12, 0.3)
    weak = st.CorrelationResult(500, 40, 0.04, -0.02, 0.10, 0.3)
    bars = []
    for i in range(12):
        row = st.judge(f"h{i}", sel, weak, ledger=led)
        bars.append(row["bar"])
        # With 40 blocks the smallest detectable effect is 0.26, so a rho of 0.04 is underpowered
        # however small the bar is. That distinction is the point: "we looked and saw nothing" and
        # "we could not have seen anything" are different sentences.
        assert row["verdict"] == "UNDERPOWERED"
        assert row["min_detectable_rho"] is not None and row["min_detectable_rho"] > abs(weak.rho)
        assert row["ledger_bar"] is not None and row["ledger_bar"] <= row["bar"]
    assert bars[0] > bars[-1] and abs(bars[0] - 0.05) < 1e-12 and abs(bars[-1] - 0.05 / 12) < 1e-12
    assert st.count_trials(led) == 12 and all(r["kind"] == "signal" for r in st.read_ledger(led))


def test_the_bar_is_counted_within_a_family_not_across_the_whole_ledger(tmp_path):
    """Two searches asking unrelated questions must not pay for each other.

    This is the defect the flat whole-ledger Bonferroni had: 119 hypotheses put the bar at 3.1e-4 and
    two effects that are real and behave as the literature predicts could not clear it - post-earnings
    drift at +0.072 (p=0.004) and trailing volatility against the next 21 days' return on SPY alone
    at +0.168 (p=0.005, same sign in 2000, 2007, 2020 and 2022).
    """
    led = tmp_path / "s.jsonl"
    sel = st.CorrelationResult(5000, 60, 0.07, 0.03, 0.11, 0.004)
    real = st.CorrelationResult(5000, 60, 0.072, 0.03, 0.11, 0.004)
    for i in range(40):  # a big, unrelated search
        st.judge(f"xs:h{i}", sel, st.CorrelationResult(5000, 60, 0.02, -0.01, 0.05, 0.2), family="xs", ledger=led)
    inside = st.judge("fundamentals:EAR->d20", sel, real, family="fundamentals", ledger=led)
    assert inside["bar"] > real.p, "the family bar should be the only multiplicity it pays"
    assert inside["verdict"] == "SURVIVES"
    assert inside["ledger_bar"] < real.p, "and the flat whole-ledger bar is still reported beside it"
    assert st.family_bar("fundamentals", led) == 1
    assert st.family_bar("xs", led) == 40


def test_an_exploratory_hypothesis_is_reported_but_can_never_survive(tmp_path):
    """A lead is publishable and chaseable without being smuggled in as a finding."""
    led = tmp_path / "s.jsonl"
    sel = st.CorrelationResult(5000, 60, 0.06, 0.02, 0.1, 0.02)
    test = st.CorrelationResult(5000, 60, 0.07, 0.02, 0.12, 0.004)
    row = st.judge("spy-only:vol->ret", sel, test, family="spy-only", kind="exploratory", ledger=led)
    assert row["verdict"] == "EXPLORATORY" and row["kind_of_test"] == "exploratory"
    assert row["test"]["p"] == 0.004, "the uncorrected p travels with it so a reader can see how close it came"
    confirm = st.judge("spy-only:vol->ret2", sel, test, family="spy-only", ledger=led)
    assert confirm["verdict"] == "SURVIVES"


def test_a_repeated_hypothesis_is_a_replication_and_does_not_raise_the_bar(tmp_path):
    """Re-running a measurement to reproduce it must not be charged as a new trial. A session that
    re-ran the 13-ETF long-history search appended 18 rows identical to four decimal places, and the
    flat bar moved from 0.05/107 to 0.05/125 in the process."""
    led = tmp_path / "s.jsonl"
    base_sel = st.CorrelationResult(500, 40, 0.03, 0, 0.06, 0.01)
    first = st.judge("long:h", base_sel, st.CorrelationResult(500, 40, 0.10, 0.05, 0.15, 0.0003), family="long", ledger=led)
    identical = st.judge("long:h", base_sel, st.CorrelationResult(500, 40, 0.10, 0.05, 0.15, 0.0003), family="long", ledger=led)
    divergent = st.judge("long:h", base_sel, st.CorrelationResult(500, 40, 0.16, 0.09, 0.23, 0.0001), family="long", ledger=led)
    assert identical["replication_of"] == "long:h" and "replication_delta" not in identical
    assert divergent["replication_of"] == "long:h" and divergent["replication_delta"] == 0.06
    assert identical["trials_so_far"] == first["trials_so_far"] == divergent["trials_so_far"]
    assert st.family_bar("long", led) == 1
    # A third hypothesis still pays for one, not for the rows the replicas added.
    assert st.judge("long:i", base_sel, st.CorrelationResult(500, 40, 0.10, 0.05, 0.15, 0.0003), family="long", ledger=led)["trials_so_far"] == 2


def test_a_null_without_power_is_underpowered_not_a_claim_of_absence(tmp_path):
    led = tmp_path / "s.jsonl"
    sel = st.CorrelationResult(500, 12, 0.05, -0.2, 0.3, 0.4)
    null = st.CorrelationResult(500, 12, 0.04, -0.3, 0.38, 0.4)
    row = st.judge("intraday:null", sel, null, family="intraday", ledger=led)
    assert row["verdict"] == "UNDERPOWERED"
    assert row["min_detectable_rho"] is not None and row["min_detectable_rho"] > 0.4


def test_an_unfiled_family_falls_back_to_the_whole_ledger_rather_than_escaping(tmp_path):
    led = tmp_path / "s.jsonl"
    for i in range(9):
        st.judge(f"h{i}", st.CorrelationResult(900, 40, 0.0, 0, 0.04, 0.3), st.CorrelationResult(900, 40, 0.02, -0.02, 0.06, 0.4), family="xs", ledger=led)
    row = st.judge("nofamily", st.CorrelationResult(900, 40, 0.0, 0, 0.04, 0.3), st.CorrelationResult(900, 40, 0.02, -0.02, 0.06, 0.4), ledger=led)
    assert row["family"] == "unfiled"
    assert abs(row["bar"] - 0.05 / 10) < 1e-12, "an unfiled hypothesis inherits the whole ledger's bar"


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
