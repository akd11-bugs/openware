# Architecture

This describes what exists today. It is a prototype: state lives in memory and the API runs as one process.
Diagrams use Mermaid, which GitHub and VS Code render.

## System as built

```mermaid
flowchart LR
    C([Fintech customer]) -->|POST /v1/assess<br/>API key, one tenant| API[Scoring API<br/>FastAPI]
    API --> V{Signed agent<br/>request?}
    V -->|yes| AV[Agent verifier<br/>Ed25519, fail closed]
    V -->|no| FS
    AV --> FS[Online feature store<br/>per tenant, in memory]
    FS --> M[LightGBM model<br/>saved model + card]
    M --> T[Tenant thresholds<br/>false alarm budgets]
    AV --> D
    T --> D[Decision<br/>allow, challenge, block<br/>reason codes]
    D --> C
    D --> AU[(Audit log<br/>hashed ids)]
    C -->|POST /v1/feedback| API
    FS --> DR[Drift monitor<br/>PSI, alert rate]
```

## One request

```mermaid
sequenceDiagram
    participant F as Fintech
    participant A as API
    participant V as Agent verifier
    participant S as Feature store
    participant M as Model
    F->>A: transaction, optional signed agent request
    A->>A: check API key, validate input, lock
    A->>V: verify signature, nonce, time window
    V-->>A: verified, invalid or unverified
    A->>S: read history, velocity, device sharing
    S-->>A: features (earlier events only)
    A->>M: score
    M-->>A: risk score
    A->>A: compare with tenant thresholds, add reasons
    A->>S: record the event
    A-->>F: action, score, reasons, latency
```

## Fast path and slow path

| | Fast path (per transaction) | Slow path (offline) |
| --- | --- | --- |
| What | Verify, read features, score, decide | Build features, train, calibrate, evaluate |
| Where | `agentshield/serving`, `features_online`, `agent_trust` | `agentshield/txn`, `scripts/train_serving.py` |
| Speed | p50 0.8 ms over local HTTP | Minutes |
| Output | A decision with reasons | A versioned model and model card |

## Decisions we made, and why

- **Time split with a label delay gap.** Real fraud labels arrive late, and random splits leak the future.
- **Every feature looks backwards only.** Each one has a test that changes later rows and checks nothing moves.
- **The real-time store must match the batch code.** A parity test compares them row by row.
- **State is per tenant.** A security review found that one shared store let customers affect each other.
- **Server time, not client time.** A forged timestamp could wipe a customer's history.
- **Anything unknown means challenge or block.** Scoring errors, a missing verifier or a NaN score never allow.
- **Scores are compared strictly above the threshold,** the same rule used when the thresholds were calibrated.

## What is prototype and what production needs

| Today | Production |
| --- | --- |
| In-memory feature and result state (nonces can already use Redis) | Redis for features and results too, shared across workers |
| One worker process | Several workers behind a proxy |
| Records written straight from the request | Kafka for events, a data lake for training |
| Model trained on public US card data | Retrained on each customer's own labels in shadow mode |
| No rate limits | Limits and body size caps at a proxy |
| Agent keys from a registry file or a fetched key directory with signature checks, tested against a live public directory | The same, with monitoring and a per-tenant allowlist of directories |
