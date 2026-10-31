"""Load keys from a registry shaped like Visa's public Trusted Agent Protocol sample.

The sample's `agents` and `agent_keys` tables hold public keys only. Three of its habits matter for safety:
- RSA keys are labelled `RSA-SHA256` although the sample agent signs RSA-PSS, so that label maps to rsa-pss-sha256.
- flags such as is_active are the strings "true" and "false".
- key ids are only unique per agent, and the sample proxy resolves a bare key id to whichever row comes first. A key id
  used by several agents therefore resolves for none of them here, and is reported.
Nothing is written to the database.
"""
import base64
import binascii
import sqlite3
from pathlib import Path

from .verifier import KeyRegistry

ALGORITHMS = {"ed25519": "ed25519", "rsa-sha256": "rsa-pss-sha256", "rsa-pss-sha256": "rsa-pss-sha256"}


def load_visa_registry(db_path) -> tuple[KeyRegistry, dict]:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(path)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select a.id, a.name, a.is_active, k.key_id, k.public_key, k.algorithm, k.is_active "
            "from agent_keys k join agents a on a.id = k.agent_id order by k.id"
        ).fetchall()
    finally:
        con.close()
    owners: dict[str, set] = {}
    for agent_id, _name, _aa, key_id, *_ in rows:
        owners.setdefault(key_id, set()).add(agent_id)
    registry, loaded, skipped = KeyRegistry(), [], {}
    for agent_id, name, agent_active, key_id, public_key, algorithm, key_active in rows:
        if len(owners[key_id]) > 1:
            skipped[key_id] = "ambiguous key id (used by several agents)"
        elif str(agent_active).lower() != "true":
            skipped[key_id] = "agent inactive"
        elif str(key_active).lower() != "true":
            skipped[key_id] = "inactive"
        elif str(algorithm).lower() not in ALGORITHMS:
            skipped[key_id] = "unsupported algorithm"
        else:
            alg = ALGORITHMS[str(algorithm).lower()]
            try:
                raw = base64.b64decode(public_key, validate=True) if alg == "ed25519" else str(public_key).encode()
                registry.register(name, key_id, alg, raw)
                loaded.append(key_id)
            except (ValueError, binascii.Error) as e:
                skipped[key_id] = str(e) if "2048" in str(e) else "invalid key"
    return registry, {"loaded": loaded, "skipped": skipped}
