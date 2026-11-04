# Running AgentShield in production

This sets up one server with the service, Redis and a proxy. It is tested on one machine with self-signed certificates. It is built for a shadow-mode pilot, not for large scale (see "Limits today").

## What you need

- A Linux server with Docker and Docker Compose, ports 80 and 443 open.
- A domain name pointing at the server, for example `api.yourcompany.com`.
- A TLS certificate for that name (for example from Let's Encrypt), as `fullchain.pem` and `privkey.pem`.
- A trained model folder in `models/` (for example `models/portable_v1`). Models are not in git. Train one with `scripts/train_serving.py --kind portable2` (needs the datasets), or copy a folder over.
- The code: `git clone` this repository on the server.

## Steps

1. **Certificates.** Create `deploy/certs/` and put `fullchain.pem` and `privkey.pem` in it. This folder is git-ignored.
2. **Proxy name.** In `deploy/nginx.conf`, replace `api.example.com` with your domain.
3. **Customers.** Copy `deploy/tenants.example.json` to `deploy/tenants.json` and edit it: one entry per customer, with the sites and pages its agents may use. For each customer make a key and its hash:
   ```
   python -c "import secrets,hashlib;k=secrets.token_urlsafe(32);print(k,hashlib.sha256(k.encode()).hexdigest())"
   ```
   Put only the **hash** in `tenants.json` and give the customer the **key**, once, over a safe channel.
4. **Settings.** Copy `deploy/production.env.example` to `deploy/production.env`. Replace `AGENTSHIELD_AUDIT_SALT` with a long random string. This file is git-ignored.
5. **Start.** `docker compose up -d --build`
6. **Check.**
   - `docker compose ps` shows the API as healthy.
   - `curl https://api.yourcompany.com/healthz` returns `{"status":"ok"}`.
   - `curl https://api.yourcompany.com/readyz` returns `{"status":"ready"}`. It turns to an error if Redis, which blocks copied requests, is unreachable.
   - Send one real `/v1/assess` call with a customer key.
7. **Pilot.** Give the customer [examples/shadow_middleware.py](../examples/shadow_middleware.py) and [integration.md](integration.md), and start in shadow mode.
8. **Summaries.** Run `python scripts/shadow_report.py <their log>` for the weekly numbers.

Rotating a key, rolling back and the health checks are in [deploy.md](deploy.md).

## Getting a trusted certificate (written, not run here)

The compose setup publishes port 80 from the API container, so the usual "standalone" Let's Encrypt method cannot use it. Two options that work:
- **DNS challenge:** `certbot certonly --manual --preferred-challenges dns -d api.yourcompany.com` (or the certbot plugin for your DNS provider, which also renews automatically). Copy `fullchain.pem` and `privkey.pem` from `/etc/letsencrypt/live/api.yourcompany.com/` into `deploy/certs/`, then `docker compose restart nginx`.
- **Certificate from your cloud provider** (or put a provider load balancer with TLS in front and keep ours as a second layer).

Certificates expire (Let's Encrypt after 90 days): put the renewal date in a calendar, or use a plugin that renews and copies the files.

## What was tested, and what was not

| Piece | Status |
| --- | --- |
| API, one worker, in a container | Built and run |
| nginx: TLS, 64 KB body cap, rate limits (200/s per key, 50/s per address when no key), HTTP to HTTPS redirect | Run in Docker against a real API with a self-signed certificate |
| Redis replay protection | Run against a real Redis 7, including an outage (fails closed) and recovery |
| `/healthz` and `/readyz` | Run, including Redis down and back |
| Audit log survives a restart | Run |
| Many tenants and key rotation | Tested in code, and reviewed by a read-only security review (its findings were fixed: duplicate tenant ids, empty list, misspelt fields) |
| Two API processes at once, each customer pinned to one by nginx | Run locally with plain uvicorn and nginx in Docker, with a shared Redis: a request replayed on the other process was refused. The compose file itself runs one API process |
| Alerts | `scripts/watch.py` decision logic tested. Not run against a real webhook |
| Backups | `scripts/backup.sh` run on the stack: Redis data, audit log and config copied, folder readable by you only |
| Log sizes | Docker keeps at most 5 files of 10 MB per service. Applied and checked. Sending logs to a central place is **not** built |
| Data protection | A [draft for a lawyer](data-protection.md) is written. **Not reviewed** |
| A trusted certificate on a real domain, a public server | **Not done** (steps above, untested) |
| A Redis restart or a network failure in the middle of a request | **Not done** |
| Central log shipping, a hosted monitoring service, encryption of backups | **Not done** |
| A lawyer's data protection review | **Not done** (needed before any customer data) |

## Limits today

- **One worker per API process.** Feature and result state is in memory, so the service refuses to start with several workers. More than one process works only if each customer always reaches the same one (the nginx upstream does this by API key; add a `server` line per process). State is split between processes, not shared. Rotating a key moves that customer to another process for a while, and its fraud features start cold there. If a process dies, its customers move and start cold. The compose file runs one process, because nginx shares its network; scaling it needs nginx on its own network (not built).
- **A restart starts the fraud features cold.** For a while the fraud and anomaly side products raise fewer alerts. Load recent history with `AGENTSHIELD_WARM_START` to avoid it. The agent check itself is not affected.
- **Rate limits are per server.** Behind a load balancer with several proxies, each applies its own limit.
- **Redis keeps its data in a volume** (`appendonly`), so copied-request protection survives a Redis restart.

## What to watch once live

- `/readyz` returns 200 (check it every minute from outside).
- The share of `429` (rate limited) and `5xx` responses in the nginx log.
- The `challenge` and `block` share in the audit log. A sudden jump can mean an attack, a partner change or a bug.
- The audit log volume size (`auditlog`), which grows without limit today.
- Run `python scripts/watch.py https://api.yourcompany.com <webhook url>` on a **different** machine for a message when `/readyz` fails.
- Run `sh scripts/backup.sh` on a schedule (for example daily from cron) and copy the folder off the server.
