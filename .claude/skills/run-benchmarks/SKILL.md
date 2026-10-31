---
name: run-benchmarks
description: Rerun the rules baseline and the LightGBM variants and summarise the results table. Use when the user asks to rerun, refresh or compare benchmark results.
disable-model-invocation: true
allowed-tools: Bash(.venv/bin/python scripts/run_baseline.py) Bash(.venv/bin/python scripts/train_ieee.py *) Bash(.venv/bin/python -m pytest *) Read
---
1. Run `.venv/bin/python -m pytest -q`; stop and report if anything fails.
2. Run `.venv/bin/python scripts/run_baseline.py`, then `.venv/bin/python scripts/train_ieee.py` (about 2 minutes; run it in the background).
3. Report one table: variant, AUC, recall at 1% and 0.1% FPR, precision at top 1%, latency. Headline variant is B_history; A_raw is the floor; C_uid is winner-style and labelled non-production.
4. State plainly that the held-out split is not comparable to the Kaggle private leaderboard (winner 0.9459).
5. Do not submit to Kaggle or commit anything.
