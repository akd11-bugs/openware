# Visa TAP sample and Web Bot Auth: review and verifier requirements

Date: 2026-10-29. Purpose: ground the AgentShield verifier design (segment 1) in public specs and code.

## Sources and scope
- Visa TAP sample repo, commit `16d59bd` (2025-10-28): `cdn-proxy/server.js`, `agent-registry/main.py`, `agent-registry/models.py`, plus READMEs. Read only, never executed.
- Web Bot Auth: `draft-meunier-webbotauth-httpsig-directory-00` and `draft-meunier-webbotauth-httpsig-protocol-02` (first ~100k of ~112k characters read). Both are Internet-Drafts and still change.
- This is a **demo/sample** repo, not Visa's production verifier. Findings are about gaps a team could copy into production, not claims about Visa's systems.

## Code review of the Visa sample (all verified in source)

| # | Finding | Where | Why it matters for us |
| --- | --- | --- | --- |
| 1 | `created`/`expires` are optional and `created` is never checked for age. Only "created > now+60s" and "expires < now" are rejected. A signature with neither parameter has no time bound. | server.js:472-483 | Freshness must be mandatory: require both, cap lifetime, bound clock skew both ways. |
| 2 | Nonce is recorded **before** signature verification, keyed globally (not per key), in an unbounded in-memory Map with a 1 h TTL. | server.js:155-167, 505-506 | Anyone with a valid `keyId` can fill memory with junk nonces. Record only after a valid signature, scope per (keyId), bound size, use a shared atomic store (Redis `SET NX PX`). |
| 3 | In-memory nonce store: not shared across instances, lost on restart. | server.js:156 | Replay window reopens on restart or on a second instance. |
| 4 | Covered components are whatever the signer declares. Verifier never requires `@authority`, `@path` or `@method`. | server.js:222-225, 263-288 | A signature over a thin component set is accepted. Enforce a minimum covered set per tag. |
| 5 | `@path` is built from `req.url`, which includes the query string. RFC 9421 `@path` excludes it. | server.js:512 | Interoperability bug and a spec deviation. |
| 6 | `tag` is parsed but never checked. `agent-browser-auth` vs `agent-payer-auth` is not enforced per route. | server.js:254 (only logged) | Browsing signatures must not authorise payment endpoints. Enforce tag-to-route policy. |
| 7 | Algorithm taken from the signed params, not from the registry key record. | server.js:558-566 | Bind `alg` to the key's registered algorithm to prevent algorithm confusion. |
| 8 | Enforcement is a prefix allowlist: only URLs starting with `/product/` are checked; `/api` and everything else pass unsigned. | server.js:643-662, 666 | Default-deny is the safe posture. Route-based opt-in is fail-open by omission. |
| 9 | Non-standard parameter name `keyId` (RFC 9421 uses `keyid`), hard-coded label `sig2`, regex parsing instead of Structured Fields. | server.js:216, 229 | Parser differentials and interop breakage. Use a real RFC 8941/9421 parser. |
| 10 | Error pages leak details: key IDs, expiry time, and "nonce previously used at <time>". | server.js:455, 482, 501 | Replay oracle and info disclosure. Return coarse reason codes. |
| 11 | Missing `alg` throws and returns 500 (fail-closed but wrong class). Full signatures and base strings are logged; rate limiting is commented out "for debugging"; the key cache TTL is `5` ms, so effectively no cache. | server.js:108-146, 153, 558 | Hygiene items for our own build. |
| 12 | Registry has no authentication: register, add key, update, deactivate are open, and CORS allows all origins. | main.py:34, 51, 189, 286, 320 | Key directory integrity is the root of trust. Needs authenticated writes and audit. |
| 13 | `key_id` is unique only per agent. `GET /keys/{key_id}` returns the first match across all agents and does not check the owning agent is active. | main.py:197-203, 232; models.py:37 | Key-ID squatting and ambiguous lookup. Use globally unique IDs or look up by (agent, key). Check agent and key status together. |
| 14 | No documented key-level revocation endpoint (`is_active` is a string, `"true"`). | models.py:41 | Need per-key revoke, rotation overlap, and a defined verifier behaviour for inactive keys. |

Good practices worth keeping: key-ID format validation, log sanitisation, Ed25519 key/signature length checks, HTML-escaped error output.

## Web Bot Auth requirements to adopt (draft-02 and the directory draft)
- Cover `@authority` or `@target-uri`; cover `Content-Digest` when there is a body.
- `created` and `expires` required; `expires` at most 24 h after `created`; `tag` must be `web-bot-auth`.
- `keyid` is a JWK SHA-256 thumbprint. Look keys up by (Signature-Agent URL, keyid), never by `keyid` alone.
- `Signature-Agent` must be present and covered.
- Nonce replay check must be atomic check-and-record. If nonce state is unavailable, do not claim replay was prevented.
- Key fetch: HTTPS, only HTTP 200 counts, no automatic redirects, size/key-count/timeout limits, SSRF block on private ranges, negative cache at most 5 min.
- Directory: JWKS at `/.well-known/http-message-signatures-directory`, media type `application/http-message-signatures-directory+json`, response signed (`@authority`, `content-digest`), keys with `nbf`/`exp`.
- Discovery failure means **unverified**, not **invalid**.

## Resulting verifier design decisions
1. **Three outcomes, never allow on failure:** `verified`; `invalid` (crypto, window, replay, policy failure) -> block; `unverified` (key fetch failed, state store down) -> lowest agent tier + challenge.
2. **Mandatory:** `created`, `expires`, `nonce`, `keyid`, `alg`, `tag`; lifetime cap; skew bound in both directions.
3. **Policy per tag and route:** minimum covered components (`@authority`, `@path`, `@method`, `content-digest` for bodies); browsing tags cannot authorise payment.
4. **Replay:** Redis atomic `SET key NX PX`, scoped by (tenant, keyid, nonce), written only after a valid signature; if Redis is down the result is `unverified`.
5. **Keys:** look up by (directory URL, keyid); alg must match the key record; check agent and key status together; per-key revoke.
6. **Default-deny** for any route configured as agent-protected; no prefix allowlist.
7. **Standards-compliant parsing** (RFC 8941/9421), `keyid` lowercase, `@path` without query.
8. **Coarse reason codes** to callers; full detail only in the audit log (with no raw signatures).

## Extra test vectors (in addition to the guide's list)
signature covering only `@authority`; missing `created`; missing `expires`; `expires` > 24 h; created far in the past; created in the future beyond skew; nonce reuse across keys; nonce flood memory test; nonce store down; tag/route mismatch (browser tag on payment); `alg` differs from key record; `@path` with query string; duplicate parameters; unknown covered component; inactive key; inactive agent with active key; duplicate `key_id` across agents; key fetch redirect; private-IP key URL; oversized directory; directory with unsigned key; label other than `sig2`.

## Open items
- The Visa spec pages on developer.visa.com were not fully read; check them for normative rules the sample omits.
- Final 12k characters of draft-02 unread (appendices likely).
- Sardine, Stripe Radar: public docs only; reason-code and signal patterns noted in the chat summary, not copied here.
