# How a company connects

The company's server (or gateway) sends each AI agent request to AgentShield and acts on the answer. Nothing else in the company's system changes.

```
SETUP (once)
  Company  ->  we issue a tenant and a private key
  Company  ->  tells us its domains, the pages agents may use, and which
               signature tag may reach which page (browse tag -> /product,
               pay tag -> /checkout)
  Agent    ->  publishes its public key (a key directory), or the company
               adds the key to a registry file

EVERY AGENT REQUEST
  AI agent
     | signed request
     v
  Company's server or gateway
     | copies the request details and calls  POST /v1/assess
     v
  AgentShield (behind TLS, rate limits and a size cap)
     | checks key, rules, time window, signature, replay
     v
  Answer: allow / challenge / block, with reason codes
     +-- verified   -> serve the request
     +-- unverified -> ask for extra confirmation (a person, a code)
     +-- invalid    -> block and log the reason
     +-- AgentShield unreachable -> the company decides; we advise "challenge", never "allow"

AFTER
  Company  ->  POST /v1/feedback with confirmed outcomes
  Company  ->  reviews reason codes and tunes its settings
```

## Rollout in three steps

1. **Shadow.** Call AgentShield on live traffic but ignore the answer. Everything is logged.
2. **Compare.** After one or two weeks, check what we would have blocked or challenged against what really happened. Fix the domain, page and tag rules.
3. **Enforce.** Turn on blocking for `invalid` first, then challenges for `unverified`, on a small share of traffic, then more.

## The ready-made example

[examples/shadow_middleware.py](../examples/shadow_middleware.py) is one function, `check`, that a company copies into its server.

```python
from shadow_middleware import check

action = check(
    {"transaction_id": "t1", "customer_id": "c1", "amount_minor": 5000},   # amount in paise
    {"method": "GET", "authority": "shop.example.com", "path": "/product/1", "query": "", "headers": request_headers},
    url="https://api.yourhost.com/v1/assess", api_key=KEY, shadow=True)
```

- With `shadow=True` it always returns `"allow"` and writes what would have happened to a log file (`agentshield_shadow.jsonl`). Switch to `shadow=False` to enforce.
- If AgentShield is unreachable or errors, it returns `"challenge"`, never `"allow"`.
- The log holds ids, the decision and reason codes. It never stores signatures, headers or keys.

## What the company needs to build

- One hook in its server that calls `check` for agent traffic and acts on the three answers.
- A step-up path for `unverified` (a human confirmation or a one-time code).
- A rule for what to do if AgentShield does not answer in time.
