#!/bin/sh
# Back up Redis, the audit log and the config to backups/<time>/. The folder holds secrets (customer key hashes, settings), so it is
# readable by you only: copy it somewhere safe and encrypted. Run from the repo root while the stack is up.
set -eu
umask 077
d="backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$d"
docker compose exec -T redis redis-cli SAVE >/dev/null
docker compose cp redis:/data "$d/redis"
docker compose cp api:/audit "$d/audit"
cp "${AGENTSHIELD_TENANTS_FILE:-deploy/tenants.json}" "${AGENTSHIELD_ENV:-deploy/production.env}" "$d/"
echo "backed up to $d"
