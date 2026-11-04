"""Alert when the API stops being ready: python scripts/watch.py https://api.yourcompany.com WEBHOOK_URL
Checks /readyz every 30 s, sends one message after 3 failures in a row and one when it recovers. The webhook gets
{"text": "..."} (Slack-style). Run it from a DIFFERENT machine than the API, or the alert dies with the server."""
import sys
import time

import httpx


def step(state, ok, need=3):
    """state is (consecutive failures, down alert sent). Returns (new state, "DOWN" | "RECOVERED" | None)."""
    fails, alerted = state
    if ok:
        return (0, False), ("RECOVERED" if alerted else None)
    fails += 1
    return (fails, alerted or fails >= need), ("DOWN" if fails == need else None)


def main(base, hook, every=30):
    state = (0, False)
    while True:
        try:
            ok = httpx.get(base.rstrip("/") + "/readyz", timeout=5).status_code == 200
        except httpx.HTTPError:
            ok = False
        state, msg = step(state, ok)
        if msg:
            httpx.post(hook, json={"text": f"AgentShield {msg}: {base}"}, timeout=10)
        time.sleep(every)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
