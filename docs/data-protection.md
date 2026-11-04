# Data protection: draft for a lawyer or data protection officer

**Status: a draft written by the engineering side. No lawyer or data protection officer has reviewed it, and it is not legal advice.** Nothing in it should be read as a compliance claim. Items marked *to confirm* need an expert answer before any customer data is used.

## What the service receives

For each call: a transaction id, a customer id, optionally a payee id, device id and device type, the amount in paise, and, for agent requests, the agent's request details and signature headers. It does not ask for names, phone numbers, card numbers, Aadhaar or PAN.

## What is stored, and where

| Data | Where | How long (today) |
| --- | --- | --- |
| Audit record: time, tenant, transaction id, customer id **hashed with a secret salt**, decision, score, reason codes | A file in a Docker volume on the server | Forever. No automatic deletion yet |
| Per-customer history for the fraud features (customer id, amounts, times, payee and device ids) | Memory of the API process | Until restart or until it ages out of the size caps |
| Replay protection: key id and a one-time value, with no customer data | Redis | Minutes, then expires |
| Signatures, request headers, API keys | Never written to our logs. Customer keys are stored only as hashes | Not stored |
| A company's own shadow log (if they use our example) | The company's server | Their choice |
| Backups ([scripts/backup.sh](../scripts/backup.sh)) | Wherever you copy them | Your choice. They contain the audit log and key hashes |

## Security measures that exist

TLS at the proxy, request size and rate limits, API keys stored as hashes with rotation, tenant data kept apart, hashed customer ids in the audit log, a fail-closed design, and a read-only security review of the agent verifier and tenant code. Not in place: encryption at rest, access logging for who reads the audit file, formal incident response, penetration testing.

## Questions for the expert (*to confirm*)

1. Is a customer id, even hashed, personal data under India's Digital Personal Data Protection Act 2023, and are we the processor while the fintech is the data fiduciary?
2. What contract do we need with each customer (data processing terms, breach notice time, sub-processors such as the hosting provider)?
3. Do RBI rules on storing payment data in India apply to the data we hold, and must the server be in India?
4. What retention period should the audit log have, and how do we delete one person's records on request? (Today this is possible by hashing the id with the salt and filtering the file, but there is no tool for it.)
5. Is consent or notice needed from the fintech's end users for fraud checks, or does the fintech's own basis cover it?
6. What must we do, and by when, after a breach?
7. Do we need to register or appoint anyone (for example a grievance officer)?

## Engineering work that follows the answers

Automatic audit log retention and deletion, a per-customer delete tool, encryption at rest for the volume and backups, access logging, and a written incident plan.
