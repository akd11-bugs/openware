"""Time-based split. Never random: random splits leak the future into training."""
import pandas as pd


def time_split(
    df: pd.DataFrame, train_frac: float = 0.7, gap: int = 0
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train gets rows strictly before a cutoff timestamp, test gets rows at least `gap` seconds after it.

    The cutoff is a timestamp value, so rows sharing a timestamp are never separated.
    `gap` models label delay (chargebacks arrive days or weeks late); rows inside the gap are dropped.
    Returned frames keep the caller's original index labels, so they can be used to index
    parallel arrays that are in the caller's row order.
    """
    if not 0.0 < train_frac < 1.0:
        raise ValueError("train_frac must be between 0 and 1 (exclusive)")
    if gap < 0:
        raise ValueError("gap must be >= 0")
    ordered = df.sort_values("ts", kind="stable")
    cut_idx = int(len(ordered) * train_frac)
    cutoff = ordered["ts"].iloc[cut_idx]
    train = ordered[ordered["ts"] < cutoff]
    test = ordered[ordered["ts"] >= cutoff + gap]
    return train, test
