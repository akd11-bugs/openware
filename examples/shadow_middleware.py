"""Check one AI agent request with AgentShield. Copy this into your gateway or server.

shadow=True: always returns "allow", and writes what AgentShield WOULD have done to a log. Use it first.
shadow=False: returns the real answer ("allow", "challenge" or "block"). If AgentShield cannot be reached, it
returns "challenge", never "allow".
"""
import json
import time


def check(txn: dict, agent_request: dict, *, url: str, api_key: str, shadow: bool = True,
          log_path: str = "agentshield_shadow.jsonl", post=None, timeout: float = 0.5) -> str:
    if post is None:
        import httpx
        post = httpx.post
    try:
        r = post(url, json={**txn, "actor": "agent", "agent_request": agent_request}, headers={"x-api-key": api_key}, timeout=timeout)
        out = r.json() if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001 - any failure is "unknown", which is never "allow"
        out = {}
    action = out.get("action", "challenge")
    with open(log_path, "a") as f:  # ids, decision and reason codes only: no headers, signatures or keys
        f.write(json.dumps({"ts": int(time.time()), "transaction_id": txn.get("transaction_id"), "would_do": action,
                            "agent": (out.get("agent") or {}).get("outcome"), "reasons": [x["code"] for x in out.get("reasons", [])],
                            "latency_ms": out.get("latency_ms"), "reached": bool(out)}) + "\n")
    return "allow" if shadow else action
