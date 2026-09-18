"""Configuration packs.

`config/` ships with the app and stays generic — it works on any MySQL database.
`local/` is yours: checks, charts, table links and AI notes for your own databases.
It is git-ignored, so none of it is published with the project.
Items in `local/` are added to (or, for the same id, replace) items in `config/`.
"""
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(HERE, "config")
LOCAL_DIR = os.environ.get("QA_LOCAL_DIR", os.path.join(HERE, "local"))


def _read_yaml(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        data = yaml.safe_load(f)
    return default if data is None else data


def load_list(name):
    """Checks or charts from config/<name>.yaml plus local/<name>.yaml."""
    items = {}
    for folder in (CONFIG_DIR, LOCAL_DIR):
        for item in _read_yaml(os.path.join(folder, f"{name}.yaml"), []):
            key = item.get("id") or item.get("title")
            items[key] = item
    return list(items.values())


def local_file(name):
    os.makedirs(LOCAL_DIR, exist_ok=True)
    return os.path.join(LOCAL_DIR, name)


def load_links():
    """Link conventions, search aliases and topics, merged from config/ and local/."""
    merged = {"column_links": {}, "table_links": {}, "aliases": {}, "topics": {}}
    for folder in (CONFIG_DIR, LOCAL_DIR):
        data = _read_yaml(os.path.join(folder, "links.yaml"), {})
        for section in merged:
            merged[section].update(data.get(section) or {})
    return merged


def ai_notes():
    parts = []
    for folder in (CONFIG_DIR, LOCAL_DIR):
        path = os.path.join(folder, "ai_context.md")
        if os.path.exists(path):
            with open(path) as f:
                parts.append(f.read().strip())
    return "\n\n".join(p for p in parts if p)
