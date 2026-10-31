"""Download the two Indian-style Kaggle datasets into ./data (gitignored). Needs ~/.kaggle/access_token."""
import os
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
os.environ.setdefault("KAGGLEHUB_CACHE", str(DATA / ".kagglehub"))

import kagglehub  # noqa: E402

for slug in [
    "belbino/indian-banking-transactions-20192024",
    "devildyno/upi-payment-transactions-dataset",
    "jatinkhandelwal112/indian-financial-fraud-dataset",
    "mlg-ulb/creditcardfraud",
]:
    p = kagglehub.dataset_download(slug)
    files = sorted(f"{f.name} ({f.stat().st_size / 1e6:.1f} MB)" for f in Path(p).rglob("*") if f.is_file())
    print(slug, "->", p)
    for f in files:
        print("   ", f)
