# Roadmap

Last updated 2026-10-31. The meeting with investors is by 2026-11-05.

## Done

- Transaction fraud model on real card data, with a time split, a label delay gap and a held-out test run.
- Rules baselines, tuning, a three-model blend (kept out, it is 5x slower for almost no gain).
- Agent verifier: Ed25519 and RSA-PSS, signed bodies, safe key directory fetching, per-tenant policy, 132 tests.
- Anomaly detectors: amount outliers, structuring, rapid repeats, new payee.
- Real-time feature store checked against the batch code.
- Scoring API with tenant keys, thresholds and reason codes, plus fixes from a security review.
- Drift monitoring, model cards, and mule benchmarks on PaySim and IBM AML.

## Now (before 2026-11-05)

- One page summary and a five minute demo script.
- Warm up the feature store so the demo does not start cold (the replay challenged 15% of traffic against a 5% budget).
- Confirm the design partner and add any real traction or pricing to the PRD.
- Check the RBI and NPCI figures in the PRD against the primary sources.

## Next (after the meeting)

- Test mule detection on real bank data with labelled mule accounts, and build the online version of the graph features. Multi-hop features for cycles, where the gain was smallest.
- Redis for shared state, Kafka for events, and a proxy for rate limits.
- Shadow mode pilot on a fintech's own data, with per-tenant recalibration.
- Run the verifier against a live Visa sample agent, check the signature on the key directory response, and move nonce state to Redis.
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
