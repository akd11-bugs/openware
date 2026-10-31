"""Download benchmark datasets into ./data (gitignored).

Needs Kaggle credentials: KAGGLE_USERNAME and KAGGLE_KEY env vars, or ~/.kaggle/kaggle.json.
For competitions, you must also accept the rules on the competition page once.
"""
import os
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
os.environ.setdefault("KAGGLEHUB_CACHE", str(DATA_DIR / ".kagglehub"))

import kagglehub  # noqa: E402


def main() -> int:
    path = kagglehub.competition_download("ieee-fraud-detection")
    print("IEEE-CIS files at:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
