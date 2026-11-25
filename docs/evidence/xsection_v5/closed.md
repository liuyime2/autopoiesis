# v5 closed unopened (2026-11-24)

v5 (spec `a7e1e87d…`, `docs/research/specs/v5-xsection-top500.spec`) is closed **without opening its holdout**.

- **Why:** `power.md` measured that a one-year holdout under Holm over three candidates needs IR ≈ 2.4 for even odds. The three v5 candidates' development IRs are below 0.7 (`dev.md`), so opening the holdout would almost certainly have used up the window on a "not significant".
- **The user's decision (2026-11-24):** keep the window sealed and redesign. Then: one candidate with forward evidence.
- **The window 2025-11-01..2026-11-20 is reassigned to v6** (`docs/research/specs/v6-mom-top10.spec`, registered `cf952923…`) as its look 1. It is tested once only.
- **Enforcement:** `tools/run_xsection_v5.py --phase holdout` with the v5 spec now refuses (`FAMILIES["xsection-top500-v5"]["closed"]`), covered by the test `test_the_v5_holdout_is_closed_and_v6_has_no_v5_phases`.
- **v5 verdict:** closed, not tested. It makes no claim either way about its three candidates on the holdout.
