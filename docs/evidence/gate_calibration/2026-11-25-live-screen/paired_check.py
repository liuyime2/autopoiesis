# SYNTHETIC records: unit demonstration of the deployed screen, not market evidence.
from autopoiesis import offline_validation as ov
for paired in (False, True):
    # 20 days, alternating up/down; a PERFECT strategy: BUY on up days, SELL on down days.
    recs, day_up = [], {}
    for d in range(20):
        up = d % 2 == 0; day = f"2026-01-{d+1:02d}"; day_up[day] = 1.0 if up and d % 4 == 0 else (0.7 if up else 0.3)
        for k in range(3):
            recs.append(ov.DecisionRecord(cycle_id=f"{d}-{k}", action="BUY" if up else "SELL",
                        verdict=ov.GOOD_TRADE, net_return_pct=1.0, day=day, paired=paired))
    r = ov.validate(recs, strategy_id="perfect", day_up=day_up)
    print(f"paired={paired}: {r.verdict} scored={r.scored} correct={r.correct_outcomes} margin={r.day_margin} :: {r.reason}")
