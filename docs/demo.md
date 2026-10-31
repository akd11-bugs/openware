# Five minute demo

Run everything from the repo root. Nothing here needs the internet except step 4.

## 1. Agents that cheat (1 minute)

```
.venv/bin/python scripts/demo_agent_trust.py
```

Say: a genuine signed agent is allowed. A replay, a forged signature, an expired request, a browsing signature used on a payment route and a revoked key are all stopped. A key directory that is down means challenge, never allow. Verification takes about 43 microseconds.

## 2. One API call, eight situations (2 minutes)

```
.venv/bin/python scripts/demo_api.py
```

Real output from this machine:

```
situation                                                  decision   agent check        time      reasons
-----------------------------------------------------------------------------------------------------------------------------
1. A regular payment from a customer with history          allow      agent:-             0.45 ms  -
2. Twelve payments in one second (#1)                      allow      agent:-             0.11 ms  -
2. Twelve payments in one second (#8)                      allow      agent:-             0.07 ms  -
2. Twelve payments in one second (#12)                     challenge  agent:-             0.08 ms  ONLINE_BURST_1H
3. The same customer suddenly pays 50 times their usual amount challenge  agent:-             0.08 ms  ONLINE_AMOUNT_SPIKE
4. A genuine signed AI agent pays                          allow      agent:verified      0.21 ms  -
5a. A signed agent request (first time)                    allow      agent:verified      0.15 ms  -
5b. The exact same signed request sent again (replay)      block      agent:invalid       0.15 ms  AGENT_REPLAYED_NONCE
6. A forged agent signature                                block      agent:invalid       0.11 ms  AGENT_BAD_SIGNATURE
7. A signed agent acting on a domain the shop did not allow block      agent:invalid       0.07 ms  AGENT_AUTHORITY_NOT_ALLOWED
8. Someone says they are an agent but sends no signature   challenge  agent:unverified    0.07 ms  AGENT_NOT_PROVEN
```

Say: each shop (tenant) sets its own rules, including which domains and routes its agents may use. Every answer comes with a reason code a risk analyst can read. The timings are the server's own work, in process.

## 3. The numbers behind it (1 minute)

Open `docs/results/` and the table in `README.md`. Lead with the honest ones:
- Real card data, scored once on a later period: 41.7% of fraud caught at 1% false alarms against 8.0% for rules only.
- Mule detection on IBM AML: 50.4% rises to 73.9% with graph features (about 23 points, interval about 21 to 26).
- The weak one: a model that only uses fields every feed has catches about 12%. We say so, and that is why the pilot matters.

## 4. Against Visa's own code and a live directory (optional, 1 minute)

Visa's agent code (not part of this repo):

```
git clone https://github.com/visa/trusted-agent-protocol /tmp/visa
VISA_TAP_DIR=/tmp/visa .venv/bin/python -m pytest tests/test_visa_live_sample.py -q
```

Say: this runs Visa's real signing functions with fresh keys and reads their sample registry. It also showed us that their registry lets two agents share a key id called `primary`, so we refuse that key for both.

A live public key directory (needs internet): `AgentShield` fetched Cloudflare's Web Bot Auth test directory, refused private addresses, read its millisecond timestamps, and verified its real Ed25519 signature.

## Questions to expect, and straight answers
- *Is this proven on Indian payments?* No. It is proven on public data. A shadow mode pilot on a fintech's own data is the next step.
- *Why is one model weak?* It only gets fields every feed can provide. Accuracy comes from each customer's own signals and labels.
- *Can it run at UPI scale?* Scoring takes well under a millisecond in process. A real deployment needs Redis for shared state and Kafka for events, which are designed but not built.
