"""Rules-only baseline on IEEE-CIS and PaySim. Fit on the early 70% of time, score the later 30% once."""
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentshield.txn.baseline import IeeeCisBaseline, PaySimBaseline  # noqa: E402
from agentshield.txn.loaders import ieee_cis_to_standard, paysim_to_standard  # noqa: E402
from agentshield.txn.metrics import auc, precision_at_k, recall_at_fpr  # noqa: E402
from agentshield.txn.split import time_split  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
IEEE = DATA / ".kagglehub/competitions/ieee-fraud-detection"
PAYSIM = DATA / "paysim/PS_20174392719_1491204439457_log.csv"
IEEE_CATS = ["x_ProductCD", "x_P_emaildomain", "x_R_emaildomain", "x_card4", "x_card6", "x_DeviceType"]


def show(name: str, test: pd.DataFrame, s) -> None:
    y = test["label"].to_numpy()
    k = max(1, int(len(y) * 0.01))
    print(
        f"{name:34s} AUC {auc(y, s):.4f} | recall@1%FPR {recall_at_fpr(y, s, 0.01):.4f} "
        f"| recall@0.1%FPR {recall_at_fpr(y, s, 0.001):.4f} | precision@top1% {precision_at_k(y, s, k):.4f}"
    )


def main() -> None:
    tx = pd.read_csv(IEEE / "train_transaction.csv")
    ident = pd.read_csv(IEEE / "train_identity.csv")
    df = ieee_cis_to_standard(tx, ident)
    del tx, ident
    train, test = time_split(df, 0.7, gap=7 * 86400)  # same gap as train_ieee.py so test sets match
    m = IeeeCisBaseline(IEEE_CATS).fit(train)
    print("IEEE-CIS rules:", {c: sorted(map(str, v)) for c, v in m.risky.items()}, f"amount_thr={m.amount_thr:.0f} velocity_thr={m.velocity_thr}")
    t = time.perf_counter()
    s = m.score(test)
    print(f"scored {len(test):,} rows in {time.perf_counter()-t:.1f}s (batch)")
    print(f"test rows {len(test):,}, fraud {test['label'].mean():.3%}")
    show("IEEE-CIS rules baseline", test, s)

    ps = paysim_to_standard(pd.read_csv(PAYSIM))
    train, test = time_split(ps, 0.7, gap=86400)
    print(f"\nPaySim test rows {len(test):,}, fraud {test['label'].mean():.3%}")
    for use_bal in (True, False):
        m = PaySimBaseline(use_balance_fields=use_bal).fit(train)
        show(f"PaySim baseline (balance={use_bal})", test, m.score(test))


if __name__ == "__main__":
    main()
