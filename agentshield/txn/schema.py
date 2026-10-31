"""Standard transaction format every dataset or customer feed is mapped to.

ts           seconds since the start of the source data (relative, not wall clock)
amount_minor integer minor units (cents/paise); never floats
sender_id    string; a proxy (e.g. card id) when the source has no account
receiver_id  string or null when the source has no receiver
label        0 legit, 1 fraud
x_*          unmapped source fields kept as extra features
"""
import pandas as pd

STANDARD_COLUMNS = [
    "txn_id",
    "ts",
    "amount_minor",
    "sender_id",
    "receiver_id",
    "label",
    "source",
]


def validate_standard(df: pd.DataFrame) -> None:
    for col in STANDARD_COLUMNS:
        if col not in df.columns:
            raise ValueError(f"missing standard column: {col}")
    if not df["label"].isin([0, 1]).all():
        raise ValueError("label must be 0 or 1")
    if not pd.api.types.is_integer_dtype(df["amount_minor"]):
        raise ValueError("amount_minor must be an integer dtype")
    if df["ts"].isna().any():
        raise ValueError("ts must not be null")
