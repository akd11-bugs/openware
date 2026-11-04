# AgentShield

**AgentShield checks that an AI agent is really who it says it is, before a company lets it browse or pay.**

## The problem, in plain words

AI assistants are starting to shop and pay on behalf of people. A shop receiving one of those requests has a hard question: is this a real assistant from a real provider, or someone pretending to be one? A fake or stolen identity could place orders, drain accounts or copy a genuine request and use it again.

## What AgentShield does

Think of a passport check at a border. A genuine AI agent carries a digital signature, like a stamp that is very hard to forge. When a request arrives, AgentShield checks:

- **Is the stamp real?** The signature must match the agent's published key.
- **Is it fresh?** Old requests are refused, and so is a copy of an earlier one (this is called a replay).
- **Is it allowed here?** An agent with a "browse" pass cannot use it to pay. Each company decides which sites and pages its agents may use.

The answer is one of three, always with a plain reason:

| Answer | What it means | What the company does |
| --- | --- | --- |
| Verified | The agent is genuine and allowed | Lets it through |
| Unverified | We could not prove it either way | Asks for extra confirmation |
| Invalid | Forged, copied, expired or not allowed | Blocks it |

If anything goes wrong or is uncertain, AgentShield never lets the request through on its own.

## What it does not do

It proves **who** an agent is. It does not judge whether a genuine agent means well. A real, properly registered agent with bad intentions would still be marked verified. Judging behaviour is the next layer, and it needs real traffic to build and test.

## How a company uses it

1. **Sign up.** We give the company a private key for our service and set its rules: which websites and pages its agents may use.
2. **Add one check.** The company's server sends each agent request to AgentShield and acts on the answer. We provide a ready-made example for this.
3. **Start in shadow mode.** For the first weeks the company only watches what AgentShield would have done, and blocks nothing. When the results look right, it switches on blocking for a small share of traffic, then more.

See [docs/integration.md](docs/integration.md) for the full flow.

## Where it stands today

- **Working prototype.** It is built and tested, but no real customer uses it yet.
- **Tested against real examples.** It runs Visa's own sample signing code and reads a live public key directory. Forged, copied, expired and tampered requests are all stopped.
- **Fast.** One check takes a fraction of a millisecond.
- **Ready to try with partners.** The setup for one server is written, and the proxy, health checks and Redis (the shared memory that blocks copied requests) have been tested on a real machine.
- **Not yet proven:** real traffic from real companies, and use at large scale. There is no Indian customer data in it yet, and no lawyer has reviewed how it handles data ([draft](docs/data-protection.md)). The next step is a shadow-mode pilot with two or three companies.

## Also included

Two smaller tools share the same service and are not the launch product. **Transaction fraud scoring** rates how risky a payment looks. **Anomaly detectors** flag unusual behaviour such as a sudden large amount or many payments in a second. Both are built and measured on public data, and both need a company's own data to become accurate. Their results are in the engineering section below.

## Words you may hear

- **Agent:** software that acts for a person, such as a shopping assistant.
- **Signature:** a digital stamp on a request that proves who sent it.
- **Replay:** reusing a copy of an earlier genuine request.
- **Shadow mode:** AgentShield watches and records, but blocks nothing.
- **Tenant:** one customer company using the service, with its own key and rules.
- **False alarm:** a good request wrongly flagged.

## Documents

- [docs/summary.md](docs/summary.md): one page overview
- [docs/integration.md](docs/integration.md): how a company connects
- [docs/production-setup.md](docs/production-setup.md): how to run it on a server
- [docs/prd.md](docs/prd.md): product plan, [roadmap.md](roadmap.md), [changes.md](changes.md), [architecture.md](architecture.md)

---

# For engineers

## Results so far

| What | Result | Notes |
| --- | --- | --- |
| **Main product: agent verification** | 185 tests, about 43 microseconds per Ed25519 check and 19 per RSA-PSS | Run against Visa's own signing code and sample registry, and a live public key directory. Forged, replayed, expired and body-swapped requests are blocked |
| API over local HTTP | p50 0.8 ms, p99 1.4 ms, about 1,200 requests per second | Excludes a network hop and a real Redis lookup |

The Kaggle winner scored 0.9459 on a hidden test set. Ours is a different test, so the two numbers are not comparable. On a fresh clone with no data, 396 tests pass and 21 skip (6 need the cached dataset, 9 need a checkout of Visa's sample, 6 need a live Redis). All 417 pass with all three.

## Also in the repo

Transaction fraud scoring, anomaly detectors and a mule detection benchmark use the same API and feature store. They are secondary and not the launch product.

| What | Result | Notes |
| --- | --- | --- |
| Fraud model, all card fields | AUC 0.903, catches 41.7% of fraud at 1% false alarms | IEEE-CIS, later time period, scored once. Rules-only baseline: 0.730 and 8.0% |
| Fraud model for the API (23 portable fields) | AUC 0.788, catches 11.8% at 1% false alarms | Only uses fields any feed can supply |
| Mule detection, account graph features | PaySim: AUC 0.971, catches 75.2% at 1% false alarms (without graph features 0.944 and 50.5%). IBM AML: AUC 0.982 and 73.9% (without 0.946 and 50.4%) | Both datasets are synthetic. Gains are largest for fan-in, fan-out and gather-scatter rings and small for cycles |
| Anomaly detector, rapid repeats | Fraud is 2.2x more common in flagged rows | Real card data, flags 6% of traffic, so it is noisy |

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
- Visa's public sample differs from the standard in small ways (a camel-case key id, no method signed, two agents sharing a key id). Our strict verifier rejected it out of the box, so each difference is now an explicit setting.
- A real public key directory signs only the host, not the body, and writes timestamps in milliseconds. Our first parser rejected it, so those are explicit settings now.
- We said a cold feature store over-alerted (15%). A controlled comparison on the same window showed the opposite: 3.6% cold against 5.0% warm. The 15% came from the unusual first days of the dataset.

## What we can still do

- Launch agent trust to two or three design partners behind a proxy with TLS and rate limits, with Redis tested live and shared across workers.
- Run the side products in shadow mode on a real fintech's data. This is the biggest gap for them, since nothing here is Indian traffic.
- Use richer fields such as payee history, device IDs and KYC status.
- Test mule detection on real bank data with labelled mule accounts, and build the online version of the graph features.
- Move the feature and nonce state to Redis, and add Kafka.
- Add rate limits at a proxy (config written, not tested), and share feature state across workers.
- Try Isolation Forest next to the rule detectors.
- Get a proper data protection review before touching customer data.

## Try it

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python scripts/demo_agent_trust.py
.venv/bin/python scripts/demo_api.py   # needs a trained model, see below
```

Datasets download with `scripts/download_data.py` and `scripts/download_india.py` and need Kaggle credentials. To run the API, train a model with `scripts/train_serving.py --kind portable2`, set `AGENTSHIELD_API_KEY` (32 characters or more), then start `uvicorn agentshield.serving.main:app` with a single worker. For many customers, use `AGENTSHIELD_TENANTS` instead (one key hash list per tenant, with key rotation). To run it on a server with Docker (API, Redis and nginx), follow `docs/production-setup.md`. Key rotation and rollback are in `docs/deploy.md`.

## Layout

- `agentshield/txn` fraud features, loaders, baselines and the experiment harness
- `agentshield/anomaly` rule based anomaly detectors
- `agentshield/agent_trust` the signature verifier, key directory fetcher and Redis nonce store
- `agentshield/features_online` the real-time feature store
- `agentshield/serving` the API
- `agentshield/monitoring` drift checks
- `docs` the PRD, research notes and result logs

## Notes

Datasets are public and several are synthetic, so fraud lift on those says little about real accuracy. See `docs/prd.md` for the product plan and `docs/research` for the agent protocol review. Licensed under Apache-2.0 (see `LICENSE`). Report security issues as described in `SECURITY.md`.
