"""Terminal demo: signed AI-agent requests -> verified / challenge / block, plus verification latency.

  .venv/bin/python scripts/demo_agent_trust.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentshield.agent_trust.signer import public_bytes, sign_request  # noqa: E402
from agentshield.agent_trust.verifier import InMemoryNonceStore, KeyRegistry, Policy, Request, Verifier  # noqa: E402

HOST, NOW = "shop.example.in", 1_700_000_000
POLICY = Policy(tag_routes={"agent-browser-auth": ("/product",), "agent-payer-auth": ("/checkout",)},
                public_prefixes=("/health",))


def main() -> None:
    good, other = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    reg = KeyRegistry()
    reg.register("shopping-agent", "key-good", "ed25519", public_bytes(good))
    reg.register("revoked-agent", "key-old", "ed25519", public_bytes(other))
    reg.revoke_key("key-old")
    v = Verifier(reg, InMemoryNonceStore(), POLICY, clock=lambda: NOW)

    def s(key=good, kid="key-good", path="/product/42", tag="agent-browser-auth", **kw):
        return sign_request(key, kid, method="GET", authority=HOST, path=path, tag=tag, now=kw.pop("now", NOW), **kw)

    replay = s(nonce="replay-demo-0001")
    cases = [
        ("Genuine agent browsing a product page", s(), "/product/42"),
        ("Genuine agent paying at checkout", s(path="/checkout/pay", tag="agent-payer-auth"), "/checkout/pay"),
        ("Same signed request sent AGAIN (replay)", replay, "/product/42"),
        ("  ...first send of that request", None, None),
        ("Forged: signed with the wrong private key", s(key=other), "/product/42"),
        ("Expired signature (sent 20 minutes late)", s(now=NOW - 1200), "/product/42"),
        ("Signature lifetime 1 hour (over the cap)", s(lifetime=3600), "/product/42"),
        ("Browsing signature used on the PAYMENT route", s(path="/checkout/pay"), "/checkout/pay"),
        ("Signature does not cover the path", s(covered=("@method", "@authority")), "/product/42"),
        ("Revoked key", s(key=other, kid="key-old"), "/product/42"),
        ("Unknown agent key", s(key=other, kid="never-registered"), "/product/42"),
        ("No signature on a protected route", {}, "/product/42"),
        ("Public health check, no signature needed", {}, "/health"),
    ]
    print(f"{'scenario':48s} {'outcome':12s} {'action':10s} reason")
    print("-" * 100)
    v.verify(Request("GET", HOST, "/product/42", replay))  # the first send, so the demo's second send is a replay
    for name, headers, path in cases:
        if headers is None:
            continue
        r = v.verify(Request("GET", HOST, path, headers))
        print(f"{name:48s} {r.outcome:12s} {r.action:10s} {r.reason}")
    reg.available = False
    r = v.verify(Request("GET", HOST, "/product/42", s()))
    print(f"{'Key directory is down (cannot check)':48s} {r.outcome:12s} {r.action:10s} {r.reason}")
    reg.available = True

    n = 5000
    reqs = [Request("GET", HOST, "/product/42", s()) for _ in range(n)]
    lat = []
    for q in reqs:
        t = time.perf_counter()
        v.verify(q)
        lat.append((time.perf_counter() - t) * 1e6)
    lat.sort()
    print(f"\nVerification latency over {n:,} signed requests (single thread, in-process): "
          f"p50 {lat[n // 2]:.0f} us, p99 {lat[int(n * .99)]:.0f} us, max {lat[-1]:.0f} us")


if __name__ == "__main__":
    main()
