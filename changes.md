# Changes

The big changes and why we made them. Small fixes are in git history. Newest first.

## 2026-10-31

- **Mule benchmark on IBM AML.** Graph features lifted recall at 1% false alarms from 50.4% to 73.9% (AUC 0.946 to 0.982), with fan-in, fan-out and gather-scatter rings gaining most and cycles least. Why: a second dataset built around laundering rings. Both datasets are synthetic.
- **Mule benchmark on PaySim.** Account graph features beat a plain model by about 25 points of recall at 1% false alarms (AUC 0.971 against 0.944). Why: this was the decision gate for the pitch. Caveat: PaySim is synthetic, and the graph model is weaker at the strictest budget.
- **Scoring API, model cards, drift monitoring.** The API combines the agent check, real-time features, the model and per-tenant thresholds. Each model has a card with its settings and test results, and a card with test results blocks a second test run.
- **Security review fixes.** Per-tenant state, server time instead of client time, a lock around scoring, a 409 for reused transaction ids, bounded memory, a per-key nonce quota, strict header parsing. Why: the review found cross-tenant leakage and a timestamp attack in our first version.
- **Indian dataset leak removed.** A card status snapshot carried the label (0.917 AUC). The loader now excludes it, along with the rule text, outcome fields and protected attributes. The honest score is 0.838.
- **Portable model v2.** Device sharing and instrument features. Test AUC moved from 0.783 to 0.788, a small gain.

## 2026-10-30

- **Agent verifier prototype.** Fail closed, with three outcomes (verified, invalid, unverified). It closes the gaps we found in Visa's public sample. Tests were checked by breaking the code on purpose.
- **Anomaly detectors.** The first amount detector flagged 11.5% of traffic, so amounts moved to a log scale.
- **Dropped the location feature.** A payer in another city is normal for UPI and QR payments, so distance carried no signal.

## 2026-10-29

- **Fraud model and experiment harness.** LightGBM, time split with a one week gap, features that only look backwards, each with a test. Tuning added about 0.004 AUC.
- **Scope.** Transaction fraud is a thin entry layer. The real differences are agent trust, mule rings and low latency.
- **Visa and Web Bot Auth review.** Written up in `docs/research`.

## 2026-10-28

- Project docs started: roadmap, architecture, this log.
