# Deploy, rotate and roll back

One uvicorn worker on `127.0.0.1:8000`, behind nginx ([deploy/nginx.conf](../deploy/nginx.conf)) for TLS, rate limits and a 64 KB body cap. Run exactly one worker: feature and result state is in memory.

## First deploy
1. Train or copy a model folder, and keep the previous one.
2. Create `tenants.json` (see below). Keep it outside the repo.
3. Start: `AGENTSHIELD_TENANTS=/etc/agentshield/tenants.json AGENTSHIELD_MODEL_DIR=models/portable_v1 uvicorn agentshield.serving.main:app --host 127.0.0.1 --port 8000 --workers 1`
4. Check: `/healthz` returns `{"status":"ok"}`, `/readyz` returns `{"status":"ready"}` (it fails if Redis is configured and down), then one real `/v1/assess` call. With Docker, `docker compose up -d --build` does steps 3 and 4's start for you; see [production-setup.md](production-setup.md).

## Tenants and key rotation
`tenants.json` is a list. Each entry has a `tenant_id` and `key_hashes` (SHA-256 hex of the key, never the key). The first hash is the current key, the rest are old keys still accepted.

```
[{"tenant_id": "shop-a", "key_hashes": ["<new hash>", "<old hash>"]}]
```

Make a key and its hash: `python -c "import secrets,hashlib;k=secrets.token_urlsafe(32);print(k,hashlib.sha256(k.encode()).hexdigest())"`

1. Add the new hash at the front, keep the old one, restart.
2. Give the customer the new key. Both work.
3. When they confirm, delete the old hash and restart.

A hash shared by two tenants is refused at startup.

## Roll back
- **Code or model:** point back at the previous release or model folder and restart. State is in memory, so a restart starts cold: load a warm-start file (`AGENTSHIELD_WARM_START`) or expect fewer alerts for a short while.
- **Keys:** put the old hash back in `key_hashes` and restart.
- **Bad release check:** if `/healthz` fails or error rate rises in the first 10 minutes, roll back, do not debug in place.

## Not done yet
Shared state across workers, a trusted certificate on a real domain, log shipping and alerts. See [production-setup.md](production-setup.md) for the full list.
