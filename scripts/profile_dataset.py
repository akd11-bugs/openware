"""Compact profile of a CSV: shape, dtypes, nulls, low-cardinality values, label-like columns, time span.

  scripts/profile_dataset.py <csv> [--label is_fraud]
"""
import argparse

import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--label", default=None)
    a = ap.parse_args()
    df = pd.read_csv(a.path)
    print(f"rows {len(df):,}  cols {df.shape[1]}")
    for c in df.columns:
        s = df[c]
        nun = s.nunique(dropna=True)
        line = f"{c:28s} {str(s.dtype):8s} nulls {s.isna().mean():6.1%}  unique {nun:>9,}"
        if nun <= 12:
            vc = s.value_counts(dropna=False).head(12)
            line += "  " + ", ".join(f"{k}:{v}" for k, v in vc.items())
        elif pd.api.types.is_numeric_dtype(s):
            line += f"  min {s.min():.4g}  med {s.median():.4g}  p99 {s.quantile(.99):.4g}  max {s.max():.4g}"
        else:
            line += "  e.g. " + ", ".join(map(str, s.dropna().astype(str).unique()[:3]))
        print(line[:230])
    if a.label and a.label in df.columns:
        print(f"\nlabel {a.label}: rate {df[a.label].mean():.4%}  positives {int(df[a.label].sum()):,}")


if __name__ == "__main__":
    main()
