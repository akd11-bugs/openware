"""Five minute demo: eight situations through the real scoring API (in process), with the real model and agent verifier.

  .venv/bin/python scripts/demo_api.py

Needs a trained model (scripts/train_serving.py --kind portable2). Nothing here touches the network.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agentshield.agent_trust.signer import public_bytes, sign_request  # noqa: E402
from agentshield.agent_trust.verifier import InMemoryNonceStore, KeyRegistry, Policy, Verifier  # noqa: E402
from agentshield.serving.app import WarmEvent, create_app  # noqa: E402
from agentshield.serving.tenants import Tenant, TenantRegistry, hash_key  # noqa: E402

MODEL = ROOT / "models/portable2_v1"
KEY = "demo-key-" + "x" * 32
SHOP = "shop.example.com"


def main() -> None:
    if not (MODEL / "model.txt").exists():
        sys.exit("no model found: run  .venv/bin/python scripts/train_serving.py --kind portable2  first")
    agent_key = Ed25519PrivateKey.generate()
    registry = KeyRegistry()
    registry.register("shopping-agent", "agent-key-1", "ed25519", public_bytes(agent_key))
    tenants = TenantRegistry([Tenant(
        "demo", hash_key(KEY), agent_authorities=(SHOP,), agent_path_prefixes=("/product", "/checkout"),
        agent_tag_routes=(("agent-browser-auth", ("/product",)), ("agent-payer-auth", ("/checkout",))))])
    app = create_app(MODEL, tenants, Verifier(registry, InMemoryNonceStore(), Policy()))
    client = TestClient(app)
    now = int(time.time())
    app.state.warm("demo", [WarmEvent(customer_id="alice", amount_minor=50_000, timestamp=now - (9 - i) * 86400) for i in range(8)])
    n = [0]

    def call(label, body=None, quiet=False, **extra):
        n[0] += 1
        body = {"transaction_id": f"demo-{n[0]}", "customer_id": "alice", "amount_minor": 50_000, **(body or {}), **extra}
        r = client.post("/v1/assess", json=body, headers={"x-api-key": KEY}).json()
        why = ", ".join(x["code"] for x in r["reasons"]) or "-"
        agent = (r["agent"] or {}).get("outcome", "-")
        if not quiet:
            print(f"{label:58s} {r['action']:10s} agent:{agent:11s} {r['latency_ms']:6.2f} ms  {why}")
        return r

    def agent_req(path="/product/1", tag="agent-browser-auth", authority=SHOP, key=agent_key, nonce=None):
        h = sign_request(key, "agent-key-1", method="GET", authority=authority, path=path, tag=tag, now=now, nonce=nonce)
        return {"method": "GET", "authority": authority, "path": path, "query": "", "headers": h}

    print(f"{'situation':58s} {'decision':10s} {'agent check':17s}{'time':>6s}      reasons")
    print("-" * 125)
    call("1. A regular payment from a customer with history")
    for i in range(12):
        call(f"2. Twelve payments in one second (#{i + 1})", customer_id="bob", quiet=i not in (0, 7, 11))
    call("3. The same customer suddenly pays 50 times their usual amount", amount_minor=2_500_000)
    call("4. A genuine signed AI agent pays", agent_request=agent_req("/checkout/pay", "agent-payer-auth"), actor="agent")
    replay = agent_req("/checkout/pay", "agent-payer-auth", nonce="replay-demo-0001")
    call("5a. A signed agent request (first time)", agent_request=replay)
    call("5b. The exact same signed request sent again (replay)", agent_request=replay)
    forged = agent_req()
    forged["headers"]["signature"] = forged["headers"]["signature"][:-6] + "AAAA:"
    call("6. A forged agent signature", agent_request=forged)
    call("7. A signed agent acting on a domain the shop did not allow", agent_request=agent_req(authority="evil.example.net"))
    call("8. Someone says they are an agent but sends no signature", actor="agent")


if __name__ == "__main__":
    main()
