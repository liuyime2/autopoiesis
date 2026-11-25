# Pre-registration: E1/E2 of the live loop's screen (written before any number below was produced)
Object: offline_validation.validate at release 02a3d5e88ff2, day_up from the real day_direction.
Variants: deployed; "counted" (correct paired trade counts as correct, still not as executed fill).
Data: real journalled market outcomes (eligible rows with net_return_pct), per symbol. Strategies are SYNTHETIC.
History: 20 days drawn without replacement from days with >=3 outcomes, 3 outcomes per day (n=60).
Symbols: every symbol with >=20 such days; runs split evenly. 1000 runs per cell, seed = run index.
Policies: null_cycle (BUY/SELL uniform per cycle), null_day (one BUY/SELL per day), null_hold (BUY/HOLD/SELL uniform);
skill q in {0.25,0.5,1.0}: with prob q the action that grades correct on the real move, else uniform BUY/SELL.
paired in {False,True} (live rows are paired when the cycle recorded the rule action).
Criteria: E1: null PASS Wilson-95 upper <=5%, and null REJECT rate reported against the nominal ~2.3% one-sided.
E2: PASS >=80% at q=0.5; any REJECT of q>=0.25 above the null REJECT rate is a defect.
