# Changes

The big changes and why we made them. Small fixes are in git history. Newest first.

## 2026-11-01

- **Open items closed.** Visa's own signing code and sample registry now run through the verifier: RSA-PSS and Ed25519 signatures verify, and the sample registry's shared key id `primary` is refused for both agents. A live public key directory was fetched and its real signature verified. Why: until then the interop claim rested on headers we had built ourselves.
- **Key directory signatures.** A directory can be required to be signed by its own keys. Real directories sign only the host and publish timestamps in milliseconds, so accepting that is an explicit setting, and milliseconds are read as milliseconds.
- **Sandbox for model-written code.** The auto-search child process now runs in a macOS sandbox with no network, writes only to the results and cache folders, and no reading of credentials or `.env` files. It refuses to run unsandboxed elsewhere unless told to.
- **Redis nonce store and service settings.** Replay protection can live in Redis (tested against a fake client only). The service reads key directory, registry file, per-tenant policy, Redis URL and a warm-start file from the environment.
- **Warm start.** Recent history can be loaded into the feature store at startup. Correction to an earlier note: on the same window a cold store gave 3.6% challenges against 5.0% warm, so cold start under-alerts rather than over-alerts.
- **Meeting prep.** A one page summary, a five minute demo script, and `scripts/demo_api.py` with eight scenarios.

- **Agent verifier finished in five rounds.** RSA-PSS next to Ed25519 with the algorithm pinned to the key record. Signed request bodies through content-digest. A key directory fetcher that refuses private addresses, follows no redirects, caps size and key count, and caches failures for five minutes, with keys looked up by directory and thumbprint. Per-tenant agent policy in the API: pinned domains and paths, a tag to route map, no trust without a signed request. An interoperability test built on the shape of Visa's public sample. 132 tests now cover the verifier.
- **Differences from Visa's sample are explicit choices.** The sample uses `keyId`, signs only authority and path, and accepts the query string inside the path. We stay strict by default and offer a `visa_compat` setting, and a tenant can relax the required components. Spaces after semicolons are now accepted because the standard allows them.

## 2026-10-31

- **Review fixes.** Three independent reviews (code, security, tests) found real problems, and the cheap ones are fixed. Tests no longer fail on a fresh clone, and there is a CI job. Missing receivers and accounts are no longer pooled into one fake account. The API validates tenant budgets at startup, never lets a late event rewrite history, flags bursts inside one second, and survives an audit write failure. The pickle cache is hash checked. The auto-search code check is stricter and its child process no longer inherits API keys. Still open: a real sandbox for model-written code, same-second bursts in the model features, and a shared constants module.
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
