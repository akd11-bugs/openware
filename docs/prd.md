# AgentShield: Product Requirements Document (draft for investor review)

Status: draft v0.1, 2026-10-29. Items marked **TODO** need founder input; nothing in this document is an invented market or traction figure.

## 1. One-line summary
AgentShield is a risk API for fintechs that answers two questions on every login, KYC or payment event: **is this AI agent genuinely who and what it claims to be**, and **is the money going to a mule network?**

## 2. Problem
- AI agents are starting to act and pay on behalf of users. Payment networks and the web are publishing signing standards for them (Visa Trusted Agent Protocol, Web Bot Auth, AP2), but a standard only defines the signature. Someone has to enforce it safely.
- Our review of Visa's public sample verifier ([review](research/visa-tap-and-web-bot-auth-review.md)) found the gaps a fintech would inherit by copying it: optional expiry, replayable nonces held in process memory, no minimum signed components, unenforced per-use tags, route-based rather than default-deny enforcement, and an unauthenticated key registry. These are sample-code gaps, not claims about Visa's production systems, but they show the work is non-trivial.
- Separately, mule accounts that receive and cash out fraud proceeds are hard for single-transaction fraud tools to see, because the signal is in the network of accounts, not in one payment.
- Today fintechs buy device, behaviour and transaction-fraud tools. We are not aware of a product that **both** enforces agent identity safely **and** connects agent activity to mule networks. **TODO:** confirm with a competitive brief before this claim goes in front of investors.
- **TODO:** replace the "reported" India figures in the section below with numbers read from the RBI annual report tables, NPCI monthly statistics and the Lok Sabha reply.

### India context (reported figures, **to be verified against primary sources before the meeting**)
All figures below come from news reports or secondary summaries retrieved 2026-10-29. None has been checked against the RBI, NPCI or I4C primary publication, and several sources conflict, so cite them as "reported" only.

- **UPI scale:** monthly volume in 2026 is reported at roughly 21-24 billion transactions and about Rs 28-30 lakh crore ([Jan 2026](https://entrackr.com/news/upi-sets-new-monthly-record-with-217-bn-transactions-in-january-11063663), [May 2026](https://www.freepressjournal.in/tech/upi-transactions-jump-24-to-232-billion-in-may-transaction-value-nears-rs-30-lakh-crore), [Jul 2026, flagged by its source as not NPCI-confirmed](https://startuptalky.com/india-upi-transactions-july-2026/)). Our own arithmetic: ~23 bn/month is on the order of 9,000 transactions per second on average, with higher peaks. This sets the latency requirement.
- **Bank fraud, FY2024-25 (RBI data as reported):** about 23,953 incidents, down 34%, totalling about Rs 36,014 crore, with the rise in amount attributed mainly to reclassified legacy loan frauds ([Business Standard](https://www.business-standard.com/finance/news/bank-fraud-amount-triples-in-fy25-despite-drop-in-number-of-cases-rbi-125052900696_1.html), [RMA India](https://rmaindia.org/bank-fraud-losses-surge-to-%E2%82%B936014-cr-despite-drop-in-cases-rbi/)). Card and internet frauds are reported as the largest share by count (about 13,469) but a small share by value (about Rs 520 crore) ([MediaNama](https://www.medianama.com/2025/12/223-cards-internet-fraud-66-in-fy25-decline-banking-rbi/)); another breakdown gives different numbers, so the counting basis needs confirming. A parliamentary reply separately cites 1,44,855 digital-payment fraud cases worth Rs 821.88 crore ([The Print](https://theprint.in/india/banks-reported-rs-4457-cr-worth-of-digital-payment-fraud-over-11-yrs-close-to-half-of-it-was-in-2023-24/3004402/)), which is a narrower, non-comparable count.
- **Mule accounts:** a bank and I4C shared registry is reported to have flagged about 3.2 million mule accounts and helped block about Rs 25,698 crore in transactions ([The Ken](https://the-ken.com/story/flagged-3m-mule-accounts-declined-rs-25k-cr-in-fraud-transactions-then-who-knows/)). A statistics blog citing a Lok Sabha reply reports over 65 lakh cyber-fraud complaints and about Rs 55,050 crore reported lost across 2021-2025 ([source](https://cyberdefence.org.in/blog/cyber-fraud-statistics-india-2026/)); confirm against the reply itself.
- **Freezing is not recovery:** one city report found banks froze Rs 513 crore of Rs 1,242 crore lost, with only Rs 111 crore recovered ([Deccan Herald](https://www.deccanherald.com/india/karnataka/bengaluru/cybercrimes-catch-22frozen-accounts-and-recovery-woes-3280456)). This supports detecting mules before money moves rather than freezing after.
- **Caution:** the large rupee totals in bank-fraud data are mostly loan fraud, not payments. Do not present them as the payments-fraud market.

**Data availability:** real transaction-level Indian fraud data is not public. Public India-labelled UPI datasets on Kaggle appear synthetic and unverified, so we will use public global benchmarks for method validation, an India-style synthetic generator for stress and latency tests, and a design-partner pilot for real evidence.

## 3. Product
Two segments behind one API.

**Segment 1: Agent trust verification.** Deterministic, fail-closed verification of signed agent requests. No ML in the verifier. Outputs a verification state (`verified`, `invalid`, `unverified`), an agent tier, and reason codes.

**Segment 2: Mule-ring detection.** Entity-graph features (fan-in, fan-out, shared accounts and devices, distance to confirmed fraud) plus rules, evaluated first in shadow mode.

**Decision API**
- `POST /v1/assess` returns `action` (`allow` | `challenge` | `block`), `agent_tier`, `reasons[]` (coarse codes naming the rule that fired), `latency_ms`.
- `POST /v1/feedback` accepts analyst labels.
- Multi-tenant from day one: per-customer keys, data isolation, per-tenant thresholds and rules.

## 4. Who it is for
A platform for fintechs in general, not one customer. Candidate first segments: UPI apps and wallets, neobanks and lenders, payment gateways, card issuers and acquirers. **TODO:** choose the two or three to lead with. A design partner (FastFlowPe) is planned; **TODO:** confirm status. The buyer is the head of risk or fraud; technical sign-off comes from engineering.

## 5. Scope
**In (MVP)**
- Verifiers for Visa TAP and Web Bot Auth first, AP2 v0.2 after, each in its own versioned module.
- Mule detection (graph features, rules, shadow mode).
- Reason codes, append-only audit log, per-tenant config.
- Shadow mode and a comparison report.
- Plain-language decision explanations, exportable audit log.
- Analyst dashboard with per-tenant rule editor.
- Integration kit: sandbox key, sample code, decision webhook.
- Red-team harness against staging.

**Out (later phases)**
Human-vs-bot behavioural SDK, API-abuse guard, deepfake KYC, UPI Reserve Pay and ACP verifiers, ML scoring beyond shadow mode.

## 6. Verifier requirements (summary; full list in the review)
1. Never allow on failure. Crypto, time-window, replay or policy failure is `invalid` -> block. Key-fetch or state-store failure is `unverified` -> lowest tier plus challenge.
2. `created`, `expires`, `nonce`, `keyid`, `alg`, `tag` are mandatory; lifetime is capped; clock skew is bounded both ways.
3. Each tag has a policy: minimum signed components and permitted routes. A browsing signature cannot authorise a payment.
4. Replay protection is an atomic check-and-record in a shared store, written only after a valid signature, scoped per tenant and key.
5. Keys are looked up by (directory URL, keyid), the algorithm must match the key record, and agent and key status are checked together.
6. Routes configured as agent-protected are default-deny.
7. Standards-compliant header parsing; coarse reason codes to callers, detail only in the audit log.

## 7. Success metrics and acceptance criteria

| Area | Metric | Target | Status |
| --- | --- | --- | --- |
| Verifier correctness | Forged, expired, replayed, altered-mandate vectors rejected | 100% of vectors, zero fail-open paths | Not started |
| Verifier robustness | Key-store or nonce-store outage | Never returns allow | Not started |
| Mule detection | Recall at a fixed false-positive rate vs a rules-only baseline | **TODO**: set after first benchmark | Not started |
| Generalisation | Same result direction across PaySim, IEEE-CIS, Amazon FDB, Bank Account Fraud (IBM AML if available) | Lift on every dataset; report the spread | Not started |
| Fairness | Per-group false-positive rate on Bank Account Fraud | **TODO** threshold | Not started |
| Latency | `/v1/assess` p99 | < 100 ms, tested at 3x expected peak | Not started |
| Pilot | Shadow-mode recall and false-positive rate on a customer's real traffic | **TODO**: agree with design partner | Not started |

Honesty rule: public datasets (several synthetic) show the method works, not how it performs on Indian production traffic. The pilot is the proof gate.

## 8. Validation plan
- **Mule detection:** time-based split, rules-only baseline, then graph features; datasets PaySim (UPI-style transfers), IEEE-CIS, Amazon Fraud Dataset Benchmark, Bank Account Fraud (fairness), and IBM AML or Elliptic for laundering rings if access is obtained.
- **Agent trust:** no public dataset of signed agent traffic exists, so we generate vectors (valid, wrong key, expired, replayed, altered, tag mismatch, etc.) plus property-based mutation tests.
- **Hijacked agents:** replay AgentDojo and InjecAgent goals as payee-drift and over-cap scenarios (stretch).
- **Continuous:** a red-team harness against staging on every release; every miss becomes a test vector or rule.
- **Decision gate:** if graph features do not beat the baseline, the pitch leads with agent trust and mule detection moves to phase 2.

## 9. Why it can win (to be tested, not assumed)
- **Enforcement, not just standards:** safe verification is the hard part, as the sample-code review shows.
- **Auditability:** a deterministic verifier with reason codes gives risk teams and regulators a trail ML-only tools do not.
- **Network effect:** a cross-customer agent reputation ledger and shared mule signals using hashed identifiers only. Requires consent and privacy design (**TODO**: DPDP Act review).
- **Low-friction adoption:** API plus a free shadow-mode trial and report on the customer's own data.
- **Competitors to study:** Stripe Radar, Sardine, SEON, Castle, Sift, BioCatch, HUMAN, DataDome, Feedzai. **TODO:** competitive brief; Sardine's scam-coaching claims could not be verified from public docs.

## 10. Go to market and business model
1. Design partner pilot, then a reference.
2. Free 2-4 week shadow trials for 3-5 fintechs; the comparison report on their own data is the sales asset.
3. Distribution via payment gateways and PSPs.
4. Pricing options: per-assess-call tiers; platform fee plus usage; share of fraud loss avoided. **TODO:** decide after a pricing benchmark. No price points are proposed here.

Impact is sized per customer: fraud losses prevented (annual loss x recall improvement) + good volume no longer wrongly declined + review cost saved - our fee. Inputs come from each customer; no aggregate ROI is claimed.

## 11. Architecture
See [architecture.md](../architecture.md). Stack: Python 3.12, FastAPI, Redis, Kafka, Neo4j, PostgreSQL, ClickHouse; TypeScript and Kotlin SDKs; Next.js dashboard; Terraform in ap-south-1. Hard rules: no raw card or PII in code or logs, hashed identifiers, amounts as integer paise, hash exact received bytes, every change ships with tests.

## 12. Risks
| Risk | Mitigation |
| --- | --- |
| Protocols are drafts and still changing (Web Bot Auth drafts have been renamed and replaced; AP2 partly draft; NPCI agent protocol not final) | One module per protocol version; add rather than edit |
| Public benchmarks may not transfer to real fintech data | Pilot as proof gate; report spread across datasets |
| Mule detection needs real labelled data | Shadow mode on pilot data first |
| Cross-customer sharing raises privacy and regulatory issues | Hashed IDs only, opt-in, DPDP review before launch |
| Verifier bug lets attackers through | Test vectors first, hostile review subagent, security review, human diff review |
| Timeline: original guide assumed 12 weeks for the full MVP | Narrowed to two segments; realistic range 16-20 weeks including real protocol work |
| Competitors add agent verification | Speed, depth on enforcement, shared network signals |

## 13. Timeline
**Phase 1 (to 2026-11-05):** PRD, mule benchmark on available datasets, verifier prototype with test vectors, architecture and roadmap docs.
**Phase 2 (after the meeting):** platform skeleton (weeks 1-2), production verifiers (3-6), graph and mule detection with shadow mode (5-8), reputation and drift (7-9), red-team harness (9-10), ML in shadow and load test (10-12), then pilot and second customer. See [roadmap.md](../roadmap.md).

## 14. Open questions
- Which customer segments lead, and is FastFlowPe confirmed as design partner?
- Do we have any traction, pricing conversations or market figures to include?
- Access to IBM AML or Elliptic data (Kaggle credentials)?
- Which agent protocol do first customers actually see traffic for?
