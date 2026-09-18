# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Compare mapped fields between two tables joined on a key — a sample, fetched with limits, compared in memory."""
import pandas as pd

from core import api_compare as ac
from core import filters

KEY_BATCH = 500


def source_sql(spec, schema, limit):
    src = spec["source"]
    cols = [src["key"]] + [f["source"] for f in spec["fields"] if f["source"] != src["key"]]
    types = dict(zip(schema.table_columns(src["table"])["col"], schema.table_columns(src["table"])["type"]))
    conds, params = filters.conditions(src.get("filters"), types)
    conds.append(f"{filters.ident(src['key'])} IS NOT NULL")
    return (f"SELECT {', '.join(filters.ident(c) for c in dict.fromkeys(cols))} FROM {filters.ident(src['table'])} "
            f"WHERE {' AND '.join(conds)} ORDER BY {filters.ident(src['key'])} DESC LIMIT {int(limit)}", params)


def target_sql_batches(spec, schema, keys):
    tgt = spec["target"]
    cols = [tgt["key"]] + [f["target"] for f in spec["fields"] if f["target"] != tgt["key"]]
    types = dict(zip(schema.table_columns(tgt["table"])["col"], schema.table_columns(tgt["table"])["type"]))
    conds, params = filters.conditions(tgt.get("filters"), types)
    out = []
    keys = [str(k) for k in dict.fromkeys(keys)]
    for i in range(0, len(keys), KEY_BATCH):
        chunk = keys[i:i + KEY_BATCH]
        where = [f"{filters.ident(tgt['key'])} IN ({', '.join(['%s'] * len(chunk))})"] + conds
        out.append((f"SELECT {', '.join(filters.ident(c) for c in dict.fromkeys(cols))} FROM {filters.ident(tgt['table'])} "
                    f"WHERE {' AND '.join(where)}", chunk + list(params)))
    return out


def reverse_sql(spec, schema, limit):
    """Target rows whose key has no source row — checked for the newest `limit` target rows."""
    src, tgt = spec["source"], spec["target"]
    types = dict(zip(schema.table_columns(tgt["table"])["col"], schema.table_columns(tgt["table"])["type"]))
    conds, params = filters.conditions(tgt.get("filters"), types)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    inner = (f"SELECT {filters.ident(tgt['key'])} AS k FROM {filters.ident(tgt['table'])}{where} "
             f"ORDER BY {filters.ident(tgt['key'])} DESC LIMIT {int(limit)}")
    return (f"SELECT x.k AS target_key FROM ({inner}) AS x WHERE x.k IS NOT NULL AND NOT EXISTS "
            f"(SELECT 1 FROM {filters.ident(src['table'])} AS s WHERE {filters.ident(src['key'], 's')} = x.k) LIMIT 500",
            params)


def compare(spec, source_df, target_df, opt=None):
    """Returns (summary dict, details DataFrame: key, status, field, source_value, target_value)."""
    opt = opt or ac.Options()
    skey, tkey = spec["source"]["key"], spec["target"]["key"]
    groups = {}
    if target_df is not None and not target_df.empty:
        for _, row in target_df.iterrows():
            groups.setdefault(_k(row[tkey], opt), []).append(row)
    details = []
    summary = {"compared": 0, "matching": 0, "mismatched": 0, "missing_in_target": 0, "duplicate_in_target": 0}
    for _, srow in (source_df if source_df is not None else pd.DataFrame()).iterrows():
        summary["compared"] += 1
        key = srow[skey]
        matches = groups.get(_k(key, opt), [])
        if not matches:
            summary["missing_in_target"] += 1
            details.append({"key": key, "status": "Missing in target", "field": "", "source_value": "", "target_value": ""})
            continue
        if len(matches) > 1:
            summary["duplicate_in_target"] += 1
            details.append({"key": key, "status": "Duplicate in target", "field": tkey,
                            "source_value": "", "target_value": f"{len(matches)} rows"})
        trow = matches[0]
        bad = False
        for f in spec["fields"]:
            sv, tv = srow.get(f["source"], ac.MISSING), trow.get(f["target"], ac.MISSING)
            if not ac.same(sv, tv, opt):
                bad = True
                details.append({"key": key, "status": "Mismatch", "field": f"{f['source']} ↔ {f['target']}",
                                "source_value": ac.display(sv), "target_value": ac.display(tv)})
        summary["mismatched" if bad else "matching"] += 1
    return summary, pd.DataFrame(details, columns=["key", "status", "field", "source_value", "target_value"])


def _k(v, opt):
    n = ac.normalise(v, opt)
    return n if not isinstance(n, list) else str(n)


def suggest_fields(schema, source_table, target_table, source_key, target_key):
    import privacy
    s_cols = [c for c in schema.cols_by_table.get(source_table, []) if not privacy.is_secret_column(c)]
    t_cols = set(c for c in schema.cols_by_table.get(target_table, []) if not privacy.is_secret_column(c))
    skip = {source_key, target_key, "id", "created", "updated", "created_at", "updated_at"}
    return [{"source": c, "target": c} for c in s_cols if c in t_cols and c not in skip][:15]
