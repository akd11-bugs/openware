"""End-to-end latency of POST /v1/assess over real HTTP on localhost (server in a subprocess, keep-alive client).

Replays real IEEE-CIS events. Measures the full round trip as a client sees it. It does NOT include a network hop
to a remote server or a Redis lookup; the online store is in-process here.
"""
import os
import re
import secrets
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from experiment import load_df  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
PORT = 8077


def main() -> None:
    key = secrets.token_urlsafe(32)
    env = {**os.environ, "AGENTSHIELD_API_KEY": key, "PYTHONPATH": str(ROOT), "AGENTSHIELD_TRUST_CLIENT_TS": "1",
           "AGENTSHIELD_MODEL_DIR": os.environ.get("AGENTSHIELD_MODEL_DIR", "models/portable2_v1")}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "agentshield.serving.main:app", "--port", str(PORT),
                             "--log-level", "warning"], cwd=ROOT, env=env)
    try:
        c = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", headers={"x-api-key": key}, timeout=10)
        for _ in range(100):
            try:
                if c.get("/healthz").status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.2)
        df = load_df("ieee").iloc[:N]
        ok = re.compile(r"^[A-Za-z0-9._:@ /()+-]+$")
        s_ = lambda v: v if isinstance(v, str) and ok.match(v) else None  # noqa: E731
        rows = list(zip(df["txn_id"], df["sender_id"], df["amount_minor"], df["ts"], df["x_DeviceType"], df["x_DeviceInfo"],
                        df["x_ProductCD"], df["x_card6"], df["x_card4"]))
        rt, srv, actions = [], [], Counter()
        for tid, cust, amt, ts, dev, did, ch, ins, net in rows[:200]:  # warm-up
            c.post("/v1/assess", json={"transaction_id": f"w{tid}", "customer_id": str(cust), "amount_minor": int(amt),
                                       "timestamp": int(ts), "device_type": dev if isinstance(dev, str) else None,
                                       "device_id": s_(did), "channel": s_(ch), "instrument_type": s_(ins), "network": s_(net)})
        for tid, cust, amt, ts, dev, did, ch, ins, net in rows:
            body = {"transaction_id": f"b{tid}", "customer_id": str(cust), "amount_minor": int(amt), "timestamp": int(ts) + 10**7,
                    "device_type": dev if isinstance(dev, str) else None, "device_id": s_(did), "channel": s_(ch),
                    "instrument_type": s_(ins), "network": s_(net)}
            t0 = time.perf_counter()
            r = c.post("/v1/assess", json=body)
            rt.append((time.perf_counter() - t0) * 1000)
            j = r.json()
            srv.append(j["latency_ms"])
            actions[j["action"]] += 1
        rt, srv = np.array(rt), np.array(srv)
        print(f"{len(rt):,} real events over HTTP (localhost, keep-alive, one at a time)")
        print(f"  client round trip : p50 {np.percentile(rt, 50):.2f} ms   p95 {np.percentile(rt, 95):.2f} ms   p99 {np.percentile(rt, 99):.2f} ms   max {rt.max():.1f} ms")
        print(f"  server-side work  : p50 {np.percentile(srv, 50):.2f} ms   p95 {np.percentile(srv, 95):.2f} ms   p99 {np.percentile(srv, 99):.2f} ms")
        print(f"  throughput (sequential): {len(rt) / (rt.sum() / 1000):.0f} requests/s")
        print(f"  actions: { {k: f'{v / len(rt):.1%}' for k, v in actions.items()} }")
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    main()
