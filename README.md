# AgentShield

A risk API for fintechs. One call checks a payment for fraud, flags unusual behaviour, and verifies whether a signed AI agent is who it says it is. This is a working prototype built on public datasets, not a production system.

## Results so far

| What | Result | Notes |
| --- | --- | --- |
| Fraud model, all card fields | AUC 0.903, catches 41.7% of fraud at 1% false alarms | IEEE-CIS, later time period, scored once. Rules-only baseline: 0.730 and 8.0% |
| Fraud model for the API (23 portable fields) | AUC 0.788, catches 11.8% at 1% false alarms | Only uses fields any feed can supply |
| Mule detection, account graph features | PaySim: AUC 0.971, catches 75.2% at 1% false alarms (without graph features 0.944 and 50.5%). IBM AML: AUC 0.982 and 73.9% (without 0.946 and 50.4%) | Both datasets are synthetic. Gains are largest for fan-in, fan-out and gather-scatter rings and small for cycles |
| Anomaly detector, rapid repeats | Fraud is 2.2x more common in flagged rows | Real card data, flags 6% of traffic, so it is noisy |
| Agent verification | 43 attack tests pass, about 43 microseconds per check | Forged, replayed, expired and other attacks |
| API over local HTTP | p50 0.8 ms, p99 1.4 ms, about 1,200 requests per second | Excludes a network hop and a real Redis lookup |

The Kaggle winner scored 0.9459 on a hidden test set. Ours is a different test, so the two numbers are not comparable. 188 tests pass.

## How we got there

- Split by time, with a one week gap for late fraud labels. Never a random split.
- Every feature uses only earlier transactions, and each one has a test that proves it.
- LightGBM with a small tuning search, which added about 0.004 AUC.
- The real-time feature store is checked row by row against the batch code, so training and serving agree.
- Each model gets one look at the test period, and the model card records it.
- A read-only security review of the API, then fixes for what it found.

## Where we went wrong

- An early score of 0.917 on an Indian dataset was a leak. A card status field carried the label. The honest score was 0.838.
- Our first amount detector flagged 11.5% of all traffic. Amounts span orders of magnitude, so we moved it to a log scale.
- Features that hurt the big model helped the thin one, so a single verdict on a feature is not safe.
- PaySim's balance fields give fraud away (AUC 0.9999), so the mule results leave them out.
- A payer and merchant in different cities is normal for UPI, so we dropped the distance feature we had planned.
- The first API version shared state across tenants and trusted client timestamps. Both are fixed.
- Validation looked better than the test period by 0.025 AUC, because we tuned on it.
- Agent loops sometimes stopped early or kept running after we stopped them, so long searches now run as plain scripts.

## What we can still do

- Run a shadow mode pilot on a real fintech's data. This is the biggest gap, since nothing here is Indian traffic.
- Use richer fields such as payee history, device IDs and KYC status.
- Test mule detection on real bank data with labelled mule accounts, and build the online version of the graph features.
- Move the feature and nonce state to Redis, and add Kafka.
- Add rate limits, and bind agent checks to a tenant and a domain.
- Try Isolation Forest next to the rule detectors.
- Get a proper data protection review before touching customer data.

## Try it

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python scripts/demo_agent_trust.py
```

Datasets download with `scripts/download_data.py` and `scripts/download_india.py` and need Kaggle credentials. To run the API, train a model with `scripts/train_serving.py --kind portable2`, set `AGENTSHIELD_API_KEY` (32 characters or more), then start `uvicorn agentshield.serving.main:app` with a single worker.

## Layout

- `agentshield/txn` fraud features, loaders, baselines and the experiment harness
- `agentshield/anomaly` rule based anomaly detectors
- `agentshield/agent_trust` the signature verifier
- `agentshield/features_online` the real-time feature store
- `agentshield/serving` the API
- `agentshield/monitoring` drift checks
- `docs` the PRD, research notes and result logs

## Notes

Datasets are public and several are synthetic, so fraud lift on those says little about real accuracy. See `docs/prd.md` for the product plan and `docs/research` for the agent protocol review. Licence in `LICENSE`.
