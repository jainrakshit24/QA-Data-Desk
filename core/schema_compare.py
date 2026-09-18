# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Compare two database structures (live connections or saved snapshots). Metadata only — never data rows."""
import pandas as pd

KINDS = ["Missing table", "Extra table", "Missing column", "Extra column", "Type differs", "Nullable differs",
         "Index differs", "Default differs", "Foreign key missing", "Foreign key extra"]


def from_schema(schema_obj):
    return {"tables": schema_obj.tables[["name", "rows_est"]].to_dict("records"),
            "columns": schema_obj.columns[["tbl", "col", "type", "nullable", "key", "default", "extra"]]
            .astype(str).to_dict("records"),
            "fks": schema_obj.fks.to_dict("records")}


def _norm(v):
    s = "" if v is None else str(v)
    return "" if s in ("None", "nan", "NaN") else s


def diff(left, right):
    """Differences between two structures.

    Missing … = present on the left, absent on the right (e.g. dropped since a snapshot).
    Extra …   = present on the right, absent on the left (e.g. added since a snapshot).
    """
    lt = {t["name"] for t in left["tables"]}
    rt = {t["name"] for t in right["tables"]}
    rows = []
    for t in sorted(lt - rt):
        rows.append(("Missing table", t, "", "exists", "—"))
    for t in sorted(rt - lt):
        rows.append(("Extra table", t, "", "—", "exists"))

    lc = {(c["tbl"], c["col"]): c for c in left["columns"]}
    rc = {(c["tbl"], c["col"]): c for c in right["columns"]}
    both_tables = lt & rt
    for key in sorted(k for k in lc if k[0] in both_tables and k not in rc):
        rows.append(("Missing column", key[0], key[1], lc[key]["type"], "—"))
    for key in sorted(k for k in rc if k[0] in both_tables and k not in lc):
        rows.append(("Extra column", key[0], key[1], "—", rc[key]["type"]))
    for key in sorted(k for k in lc if k in rc):
        a, b = lc[key], rc[key]
        for field, kind in (("type", "Type differs"), ("nullable", "Nullable differs"), ("key", "Index differs"),
                            ("default", "Default differs")):
            if _norm(a.get(field)) != _norm(b.get(field)):
                rows.append((kind, key[0], key[1], _norm(a.get(field)) or "—", _norm(b.get(field)) or "—"))

    def fk_set(side):
        return {(f["tbl"], f["col"], f["ref_tbl"], f["ref_col"]) for f in side.get("fks") or []}
    lf, rf = fk_set(left), fk_set(right)
    for f in sorted(lf - rf):
        if f[0] in both_tables:
            rows.append(("Foreign key missing", f[0], f[1], f"→ {f[2]}.{f[3]}", "—"))
    for f in sorted(rf - lf):
        if f[0] in both_tables:
            rows.append(("Foreign key extra", f[0], f[1], "—", f"→ {f[2]}.{f[3]}"))
    return pd.DataFrame(rows, columns=["difference", "table", "column", "left", "right"])


def row_counts(left, right, min_rows=1000, min_ratio=0.5):
    """Tables in both whose approximate row counts differ a lot. Estimates only (InnoDB statistics)."""
    lr = {t["name"]: int(float(t.get("rows_est") or 0)) for t in left["tables"]}
    rr = {t["name"]: int(float(t.get("rows_est") or 0)) for t in right["tables"]}
    out = []
    for t in sorted(set(lr) & set(rr)):
        a, b = lr[t], rr[t]
        big = max(a, b)
        if big >= min_rows and abs(a - b) / big >= min_ratio:
            out.append({"table": t, "left_rows_est": a, "right_rows_est": b, "difference": b - a})
    return pd.DataFrame(out, columns=["table", "left_rows_est", "right_rows_est", "difference"])


def summary(df):
    counts = df["difference"].value_counts().to_dict() if not df.empty else {}
    return {k: int(counts.get(k, 0)) for k in KINDS}


def text_report(df, left_label, right_label, counts_df=None):
    lines = ["SCHEMA COMPARISON", "=" * 60, f"Left:  {left_label}", f"Right: {right_label}",
             "“Missing” = on the left only. “Extra” = on the right only.", ""]
    s = summary(df)
    lines += [f"{k:<22}{v}" for k, v in s.items()] + [""]
    for kind in KINDS:
        part = df[df["difference"] == kind]
        if part.empty:
            continue
        lines += [kind.upper(), "-" * 60]
        for r in part.head(500).itertuples():
            target = f"{r.table}.{r.column}" if r.column else r.table
            detail = "" if kind.endswith("table") else f"  left: {r.left}  right: {r.right}"
            lines.append(f"{target}{detail}")
        lines.append("")
    if counts_df is not None and not counts_df.empty:
        lines += ["APPROXIMATE ROW COUNTS THAT DIFFER A LOT", "-" * 60]
        lines += [f"{r.table}: {r.left_rows_est:,} → {r.right_rows_est:,}" for r in counts_df.itertuples()]
    return "\n".join(lines) + "\n"
