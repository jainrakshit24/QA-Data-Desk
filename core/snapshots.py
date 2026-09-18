# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Manual record snapshots: what one record looked like when a QA pressed "Save snapshot".

These are NOT database history — only the moments someone captured. Values are stored masked (secret columns hidden,
phones / emails masked, as a non-admin sees them). Each field also keeps a short keyed fingerprint of the real value, so
a change is detected even when the masked text looks the same, without keeping the real value.
"""
import hashlib
import hmac
import json
import math

import pandas as pd

import privacy


def _plain(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat(sep=" ") if hasattr(v, "hour") else v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    if hasattr(v, "item"):                   # numpy scalars
        return v.item()
    return v


def fingerprint(value, key):
    raw = json.dumps(_plain(value), default=str, sort_keys=True).encode("utf-8")
    return hmac.new(key, raw, hashlib.sha256).hexdigest()[:16]


def capture(table, key_column, key_value, row, key, linked_counts=None):
    masked = privacy.mask(pd.DataFrame([row]), is_admin=False).iloc[0].to_dict()
    fields = {c: _plain(masked[c]) for c in row}
    digests = {c: fingerprint(row[c], key) for c in row if not privacy.is_secret_column(c)}
    label_df = privacy.mask(pd.DataFrame([{key_column: key_value}]), is_admin=False)
    return {
        "table": table,
        "key_column": key_column,
        "key_label": str(label_df.iloc[0][key_column]),
        "key_digest": fingerprint(str(key_value), key),
        "fields": fields,
        "digests": digests,
        "linked_counts": {f"{t}.{c}": n for (t, c, _v), n in (linked_counts or {}).items() if n is not None},
    }


def compare(before, after):
    """Field by field: changed / same / added / removed. Uses fingerprints, so masked values still compare exactly."""
    rows = []
    cols = list(dict.fromkeys(list(before["fields"]) + list(after["fields"])))
    for c in cols:
        in_a, in_b = c in before["fields"], c in after["fields"]
        a, b = before["fields"].get(c), after["fields"].get(c)
        if privacy.is_secret_column(c):
            change = "hidden"
        elif not in_a:
            change = "added"
        elif not in_b:
            change = "removed"
        elif before["digests"].get(c) != after["digests"].get(c):
            change = "changed"
        else:
            change = "same"
        rows.append({"field": c, "before": _text(a) if in_a else "—", "after": _text(b) if in_b else "—", "change": change})
    fields = pd.DataFrame(rows, columns=["field", "before", "after", "change"])
    links = []
    for k in dict.fromkeys(list(before.get("linked_counts", {})) + list(after.get("linked_counts", {}))):
        a, b = before.get("linked_counts", {}).get(k), after.get("linked_counts", {}).get(k)
        if a != b:
            links.append({"linked": k, "before": a, "after": b,
                          "difference": (b - a) if a is not None and b is not None else None})
    return fields, pd.DataFrame(links, columns=["linked", "before", "after", "difference"])


def _text(v):
    return "NULL" if v is None else str(v)
