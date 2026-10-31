# AgentShield (repo: openware)
Risk API for fintechs: transaction fraud (thin entry layer), mule-ring detection and AI-agent trust verification (the moat, with low latency). Python 3.13 in `.venv`, LightGBM, pandas 3. Product docs: docs/prd.md, architecture.md, roadmap.md, changes.md.

## Commands
- Tests: `.venv/bin/python -m pytest -q` (single file: add the path)
- Data sanity report: `.venv/bin/python scripts/data_report.py`
- Rules baseline: `.venv/bin/python scripts/run_baseline.py`
- LightGBM variants: `.venv/bin/python scripts/train_ieee.py` (about 2 min; writes docs/results/ieee_cis_models.json)
- Download data: `.venv/bin/python scripts/download_data.py` (needs `~/.kaggle/access_token`)

## Hard rules
- Evaluation is by time split only (`agentshield/txn/split.py`), with a label-delay gap. Never a random split.
- Features use only earlier rows and never labels. Every new feature needs a past-only test like `tests/test_txn_features.py`.
- Amounts are integer minor units (`amount_minor`), never floats.
- Report production-valid results as the headline. Winner-style tricks (uid, client-wide aggregates, prediction averaging) are reported separately and labelled non-production.
- Our held-out split is NOT comparable to Kaggle's private leaderboard (winner 0.9459 private AUC); say so whenever comparing.
- Test data is used once per variant. Early stopping uses the validation slice only.
- Agent-trust verifiers (later) are pure deterministic code: fail closed, never allow on error; three outcomes `verified`, `invalid`, `unverified`. See docs/research/visa-tap-and-web-bot-auth-review.md.
- Never print, log or commit tokens, `.env`, or anything under `~/.kaggle`. Never commit `data/`.
- No numbers in docs/prd.md without a source or a measured run; mark unverified figures as such.

## Workflow
- Tests first (red, then green). Plan before touching more than 2 files.
- Run the leakage-reviewer subagent after any change to split, features, baseline, metrics or training scripts.
- Do not commit or submit to Kaggle unless the user asks.

## Gotchas
- pandas 3: text columns have dtype `str`, not `object`; test numeric dtype instead.
- IEEE-CIS is sorted by time and has 17k rows with tied timestamps; PaySim `step` is hours and has almost no repeat senders.
- PaySim balance columns nearly give away fraud; always report with and without them.
- IEEE-CIS files live in `data/.kagglehub/competitions/ieee-fraud-detection/`, PaySim in `data/paysim/`.
