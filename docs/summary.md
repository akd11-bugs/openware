# AgentShield in one page

**What it is.** Agent trust verification for fintechs and merchants: one call checks that a signed AI agent request is genuine, fresh, not replayed and allowed on that route. Transaction fraud scoring and anomaly detectors ride on the same API as side products. Today it is a working prototype built and tested on public data. It is not a production system and it has not seen Indian traffic.

## Why this, why now
- AI agents are starting to pay on behalf of people. Visa and others publish ways for agents to sign their requests, but a standard only describes the signature. Someone has to enforce it safely.
- When we read Visa's public sample we found the gaps a company would inherit by copying it: optional expiry, replayable requests, an unauthenticated key registry. We built a verifier that closes them.
- Mule accounts, which pass stolen money along, are hard to see one payment at a time. The signal is in the network of accounts.

## What works today (all measured, all reproducible from the repo)

| Piece | Result |
| --- | --- |
| Agent verification | Runs Visa's own signing code and reads its sample registry. RSA-PSS and Ed25519 signatures verify. Forged, replayed, expired, body-swapped and wrong-domain requests are blocked. About 43 microseconds per check. |
| Real key directory | Fetched a live public directory (Cloudflare's Web Bot Auth test) with private-address blocking, and verified its real signature. |
| Mule detection | Account graph features raise the share of fraud caught at 1% false alarms from about 50% to about 74% on two benchmark datasets (PaySim and IBM AML). |
| Transaction fraud, real card data | Catches 41.7% of fraud at 1% false alarms (rules only: 8.0%), scored once on a later period. |
| Scoring API | One call returns allow, challenge or block with plain reasons. About 0.8 ms per call over local HTTP. |

## What is not proven, said plainly
- **No Indian data.** The fraud model uses US card data. The Indian-style datasets are synthetic, and their labels follow generator rules, so their scores say little about real accuracy.
- **The mule results come from synthetic data.** Real bank data with labelled mule accounts is the missing test.
- **The model for any feed is weak.** Using only fields every feed can provide, it catches about 12% of fraud at 1% false alarms. It needs each customer's own data to be useful.
- **Redis replay protection passed tests against a real Redis 7 (one machine).** Several workers are not supported yet, because feature state is still in memory.

## How we kept ourselves honest
Splits are by time with a gap for late labels. Each model is scored once on held-out data. Every feature has a test that proves it only looks backwards. Three independent reviews (code, security, tests) found real problems, which are fixed. An early score of 0.917 turned out to be a leak and we corrected it to 0.838.

## What we need next
A pilot with a fintech, run in shadow mode on their own transactions. That is the only way to turn these benchmarks into a real accuracy number, and to show how many alerts a risk team would see each day.
