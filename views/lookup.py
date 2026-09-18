# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Find by ID: search one value in every table that has a given column."""
import pandas as pd
import streamlit as st

import db
import privacy
import ui


def render():
    st.title("Find by ID")
    st.caption("Look up one value — an order id, a user id — across every table that has that column.")
    if not ui.require_connection():
        return
    s = ui.get_schema()

    counts = s.tables_per_column
    id_like = [c for c in counts.index if (c.endswith("_id") or c in ("id", "uid", "cid"))
               and not privacy.is_secret_column(c)]

    a, b = st.columns([2, 3])
    value = a.text_input("Value to find", key="lk_value", placeholder="e.g. 10452")
    column = b.selectbox(
        "In which column?", id_like, key="lk_col", index=None,
        placeholder="Choose the column the value belongs to — e.g. campaign_id, user_id",
        format_func=lambda c: f"{c}   ·   in {counts[c]} tables")
    if not column:
        st.info("Choose a column. The same number can mean different things — 10908 as a campaign_id "
                "and as a college_id are unrelated records.")
        return

    tables = s.columns[s.columns["col"] == column][["tbl", "key"]]
    est = s.rows_est
    tables = tables.assign(rows=tables["tbl"].map(est).fillna(0).astype(int),
                           indexed=tables["key"].isin(["PRI", "UNI", "MUL"]))
    tables = tables.sort_values(["indexed", "rows"], ascending=[False, False])

    if column == "id":
        st.info("Every table has an “id” column, so pick the tables to search.")
        default = []
    else:
        default = tables[tables["indexed"] | (tables["rows"] < 200_000)]["tbl"].head(40).tolist()

    labels = {r.tbl: f"{r.tbl} · ~{r.rows:,} rows{' · 🔑' if r.indexed else ' · not indexed'}"
              for r in tables.itertuples()}
    chosen = st.multiselect(f"Tables to search ({len(tables)} have “{column}”)", tables["tbl"].tolist(),
                            default=default, key=f"lk_tables_{column}", format_func=lambda n: labels.get(n, n))
    slow = [t for t in chosen if not tables.set_index("tbl").loc[t, "indexed"] and est.get(t, 0) > 1_000_000]
    if slow:
        st.warning("These are large and not indexed on this column, so they may time out: " + ", ".join(slow))

    if st.button("Search", type="primary", disabled=not (value.strip() and chosen)):
        queries = []
        for t in chosen:
            cols = [c for c in s.cols_by_table.get(t, []) if not privacy.is_secret_column(c)]
            sql = (f"SELECT {', '.join(db.quote_ident(c) for c in cols)} FROM {db.quote_ident(t)} "
                   f"WHERE {db.quote_ident(column)} = %s LIMIT 50")
            queries.append((t, sql, [value.strip()]))
        with st.spinner(f"Searching {len(queries)} tables at once…"):
            found = ui.run_many(queries, source="find by id")
        st.session_state.lk_found = (value.strip(), column, found)

    if st.session_state.get("lk_found"):
        val, col, found = st.session_state.lk_found
        summary = pd.DataFrame([{
            "table": t,
            "rows found": (len(r["df"]) if not r["error"] else None),
            "note": r["error"] or ("50+ (showing 50)" if not r["error"] and len(r["df"]) == 50 else ""),
        } for t, r in found])
        hits = summary[summary["rows found"].fillna(0) > 0]
        st.markdown(f"**{col} = {val}** found in **{len(hits)}** of {len(found)} tables searched")
        st.dataframe(summary.sort_values("rows found", ascending=False, na_position="last"),
                     width="stretch", hide_index=True)
        for i, (t, r) in enumerate(found):
            if r["error"] or len(r["df"]):
                label = "error" if r["error"] else f"{len(r['df'])} row(s)"
                with st.expander(f"{t} — {label}"):
                    ui.show_result(r, key=f"lk_{i}", chart=False)
