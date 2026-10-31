"""Load the benchmark datasets into the standard format and print sanity stats."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentshield.txn.loaders import ieee_cis_to_standard, paysim_to_standard  # noqa: E402
from agentshield.txn.schema import validate_standard  # noqa: E402
from agentshield.txn.split import time_split  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
IEEE = DATA / ".kagglehub/competitions/ieee-fraud-detection"
PAYSIM = DATA / "paysim/PS_20174392719_1491204439457_log.csv"


def report(name: str, df: pd.DataFrame) -> None:
    validate_standard(df)
    train, test = time_split(df, 0.7)
    day = 86400
    print(f"\n=== {name} ===")
    print(f"rows: {len(df):,}   columns: {df.shape[1]} (extras: {df.shape[1] - 7})")
    print(f"fraud rate: {df['label'].mean():.4%}  ({int(df['label'].sum()):,} frauds)")
    print(f"time span: {df['ts'].min()/day:.1f} to {df['ts'].max()/day:.1f} days ({(df['ts'].max()-df['ts'].min())/day:.1f} days)")
    print(f"amount_minor: median {int(df['amount_minor'].median()):,}  p99 {int(df['amount_minor'].quantile(0.99)):,}  max {int(df['amount_minor'].max()):,}")
    print(f"unique senders: {df['sender_id'].nunique():,}   null receivers: {df['receiver_id'].isna().mean():.1%}")
    print(f"txn_id unique: {df['txn_id'].is_unique}")
    print(f"train: {len(train):,} rows, fraud {train['label'].mean():.4%}, ts up to {train['ts'].max()/day:.1f}d")
    print(f"test:  {len(test):,} rows, fraud {test['label'].mean():.4%}, ts from {test['ts'].min()/day:.1f}d")
    overlap = set(train["sender_id"]) & set(test["sender_id"])
    print(f"senders seen in both train and test: {len(overlap):,} of {test['sender_id'].nunique():,} test senders")


def main() -> None:
    tx = pd.read_csv(IEEE / "train_transaction.csv")
    ident = pd.read_csv(IEEE / "train_identity.csv")
    report("IEEE-CIS", ieee_cis_to_standard(tx, ident))
    del tx, ident
    report("PaySim", paysim_to_standard(pd.read_csv(PAYSIM)))


if __name__ == "__main__":
    main()
