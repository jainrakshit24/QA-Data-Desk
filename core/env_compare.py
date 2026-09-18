# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Compare two databases (environments): one record, one query's result, or table row counts.

Structure differences live on the Schema compare page — this compares data, always masked, and never writes.
"""
import pandas as pd

import privacy
from core import filters
from core import snapshots


# ------------------------------------------------------------------ one record
def record_sql(table, column, limit=50):
    return (f"SELECT * FROM {filters.ident(table)} WHERE {filters.ident(column)} = %s "
            f"LIMIT {int(limit)}")


def record_diff(table, column, value, row_a, row_b, key):
    """Field-by-field difference between the same record in two environments (masked + fingerprints)."""
    a = snapshots.capture(table, column, value, row_a, key) if row_a is not None else None
    b = snapshots.capture(table, column, value, row_b, key) if row_b is not None else None
    if a is None or b is None:
        missing = "left" if a is None else "right"
        present = b or a
        fields = pd.DataFrame([{"field": c, "before": "—" if missing == "left" else snapshots._text(present["fields"][c]),
                                "after": snapshots._text(present["fields"][c]) if missing == "left" else "—",
                                "change": "missing on the " + missing}
                               for c in present["fields"]], columns=["field", "before", "after", "change"])
        return fields, missing
    fields, _ = snapshots.compare(a, b)
    return fields, None


# ------------------------------------------------------------------ one query's result
def compare_results(df_a, df_b, key=None, ignore=()):
    """Compare two result sets. With a key column: rows only in A / only in B / differing fields.

    Returns a dict of DataFrames plus a summary. Values are compared as text, so 1 and '1' match.
    """
    out = {"summary": {}, "only_a": pd.DataFrame(), "only_b": pd.DataFrame(), "different": pd.DataFrame()}
    cols_a, cols_b = list(df_a.columns), list(df_b.columns)
    out["summary"] = {"rows_a": len(df_a), "rows_b": len(df_b),
                      "columns_only_a": [c for c in cols_a if c not in cols_b],
                      "columns_only_b": [c for c in cols_b if c not in cols_a]}
    shared = [c for c in cols_a if c in cols_b and c not in ignore]
    if not key or key not in shared:
        return out
    compare_cols = [c for c in shared if c != key and not privacy.is_secret_column(c)]
    a = df_a[shared].astype(str).set_index(df_a[key].astype(str))
    b = df_b[shared].astype(str).set_index(df_b[key].astype(str))
    a = a[~a.index.duplicated()]
    b = b[~b.index.duplicated()]
    only_a, only_b = a.index.difference(b.index), b.index.difference(a.index)
    out["only_a"] = privacy.mask(df_a[df_a[key].astype(str).isin(only_a)])
    out["only_b"] = privacy.mask(df_b[df_b[key].astype(str).isin(only_b)])
    both = a.index.intersection(b.index)
    rows = []
    for k in both:
        for c in compare_cols:
            va, vb = a.at[k, c], b.at[k, c]
            if va != vb:
                rows.append({key: k, "field": c,
                             "in A": privacy.mask(pd.DataFrame([{c: va}])).iloc[0][c],
                             "in B": privacy.mask(pd.DataFrame([{c: vb}])).iloc[0][c]})
    out["different"] = pd.DataFrame(rows, columns=[key, "field", "in A", "in B"])
    out["summary"]["shared_keys"] = len(both)
    out["summary"]["duplicate_keys_a"] = int(df_a[key].astype(str).duplicated().sum())
    out["summary"]["duplicate_keys_b"] = int(df_b[key].astype(str).duplicated().sum())
    return out


def key_candidates(df):
    """Columns that look usable as a key: unique in this result, id-like first."""
    out = []
    for c in df.columns:
        if privacy.is_secret_column(c):
            continue
        if df[c].astype(str).is_unique:
            out.append(c)
    return sorted(out, key=lambda c: (not (c == "id" or c.endswith("_id")), list(df.columns).index(c)))


# ------------------------------------------------------------------ row counts
def count_sql(table):
    return f"SELECT COUNT(*) AS `rows` FROM {filters.ident(table)}"


def count_table(counts_a, counts_b, tables):
    """counts_* : {table: rows or None}. Returns one row per table with the difference."""
    rows = []
    for t in tables:
        a, b = counts_a.get(t), counts_b.get(t)
        diff = (b - a) if isinstance(a, int) and isinstance(b, int) else None
        pct = round(diff * 100 / a, 1) if diff is not None and a else None
        rows.append({"table": t, "rows in A": a, "rows in B": b, "difference": diff, "% change": pct,
                     "status": _status(a, b)})
    df = pd.DataFrame(rows, columns=["table", "rows in A", "rows in B", "difference", "% change", "status"])
    return df.sort_values(["status", "table"], key=lambda s: s.map(STATUS_ORDER) if s.name == "status" else s)


STATUS_ORDER = {"missing in B": 0, "missing in A": 1, "could not count": 2, "different": 3, "same": 4}


def _status(a, b):
    if a is None and b is None:
        return "could not count"
    if a is None:
        return "missing in A"
    if b is None:
        return "missing in B"
    return "same" if a == b else "different"
