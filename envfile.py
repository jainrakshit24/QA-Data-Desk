"""Read a local .env file into the environment.

Keeps credentials out of the code and out of git: copy .env.example to .env and fill it in.
Real environment variables always win, so a container's settings are never overwritten by a stray file.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.environ.get("QA_ENV_FILE", os.path.join(HERE, ".env"))
LINE = re.compile(r"""^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$""")


def parse(text):
    """.env text → {key: value}. Supports quotes, inline comments and `export KEY=value`."""
    out = {}
    for raw in (text or "").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = LINE.match(raw)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if value[:1] in ("'", '"') and value[:1] == value[-1:] and len(value) >= 2:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        out[key] = value
    return out


def load(path=None, override=False):
    """Load the file if it exists. Returns the names that were set (never the values)."""
    path = path or ENV_PATH
    if not os.path.exists(path):
        return []
    with open(path) as f:
        values = parse(f.read())
    applied = []
    for key, value in values.items():
        if override or not os.environ.get(key):
            os.environ[key] = value
            applied.append(key)
    return applied
