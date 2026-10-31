# Roadmap

Last updated 2026-10-31. The meeting with investors is by 2026-11-05.

## Done

- Transaction fraud model on real card data, with a time split, a label delay gap and a held-out test run.
- Rules baselines, tuning, a three-model blend (kept out, it is 5x slower for almost no gain).
- Agent verifier: Ed25519 and RSA-PSS, signed bodies, safe key directory fetching and signature checks, per-tenant policy, a sandbox for model-written code, and a Redis nonce store. 185 tests, including a run against Visa's own signing code and a live public directory.
- One page summary, five minute demo script and an eight scenario API demo, plus a warm-start loader for the feature store.
- Anomaly detectors: amount outliers, structuring, rapid repeats, new payee.
- Real-time feature store checked against the batch code.
- Scoring API with tenant keys, thresholds and reason codes, plus fixes from a security review.
- Drift monitoring, model cards, and mule benchmarks on PaySim and IBM AML.

## Now (before 2026-11-05)

- Rehearse the demo end to end.
- Confirm the design partner and add any real traction or pricing to the PRD.
- Check the RBI and NPCI figures in the PRD against the primary sources.

## Next (after the meeting)

- Test mule detection on real bank data with labelled mule accounts, and build the online version of the graph features. Multi-hop features for cycles, where the gain was smallest.
- Redis for shared state, Kafka for events, and a proxy for rate limits.
- Shadow mode pilot on a fintech's own data, with per-tenant recalibration.
- Run the Redis nonce store against a live Redis server, and share feature state across workers so more than one process can run.
- Try Isolation Forest next to the rule detectors.
- Data protection review before any customer data is used.

## Later

- Human versus bot signals in the browser, API abuse guard, deepfake KYC checks.
- Cross-customer agent reputation using hashed identifiers only.
- Analyst dashboard and a per-tenant rule editor.

## Open questions

- Which two or three customer types do we lead with?
- Is FastFlowPe confirmed as the design partner?
- Can we get real labelled mule data, or do we wait for the pilot?
