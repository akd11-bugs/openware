"""Verify the Claude API key in .env works. Prints only pass/fail and metadata, never the key."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    load_env()
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    print(f"key loaded: {'yes' if key else 'no'}; length {len(key)}; prefix looks right: {key.startswith('sk-ant-')}")
    if not key:
        return 1
    import anthropic

    try:
        client = anthropic.Anthropic(api_key=key)
        r = client.messages.create(
            model="claude-sonnet-5-5",
            max_tokens=60,
            messages=[{"role": "user", "content": "Write a one-line Python function that adds two numbers. Code only."}],
        )
        print("API call: ok")
        print("model reply:", r.content[0].text.strip()[:120])
        print(f"tokens used: in {r.usage.input_tokens}, out {r.usage.output_tokens}")
        return 0
    except Exception as e:  # report the type only, never request details
        print("API call: FAILED", type(e).__name__, str(e)[:120].replace(key, "***"))
        return 2


if __name__ == "__main__":
    sys.exit(main())
