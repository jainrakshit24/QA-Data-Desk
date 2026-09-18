"""Compare two environments: the same record, the same query, or table row counts. Read-only on both sides."""
import pandas as pd
import streamlit as st

import db
import privacy
import schema as schema_mod
import store
import ui
from core import env_compare as ecmp
from core import evidence
from core import sql_explain

MAX_COUNT_TABLES = 40
SLOW_ROWS = 1_000_000        # above this an exact COUNT(*) is slow enough to warn about


def render():
    st.title("Compare environments")
    st.caption("Same record, same query or table row counts across two databases — for example staging against "
               "another staging copy. Both sides are read-only and values stay masked. "
               "Structure differences (tables and columns) are on the Schema compare page.")
    names = db.connection_names()
    if len(names) < 2:
        st.info("Add a second database connection on the Admin page first. To try this out, add the same database "
                "twice with different names — everything should then report “no differences”.")
        return
    ui.restore("env_a", "env_b")
    st.session_state.setdefault("env_a", ui.active_connection() if ui.active_connection() in names else names[0])
    st.session_state.setdefault("env_b", next(n for n in names if n != st.session_state["env_a"]))
    for k in ("env_a", "env_b"):
        if st.session_state[k] not in names:
            st.session_state[k] = names[0]
    c1, c2 = st.columns(2)
    a = c1.selectbox("A (reference)", names, key="env_a")
    b = c2.selectbox("B (compared)", names, key="env_b")
    ui.remember("env_a", "env_b")
    if a == b:
        st.warning("A and B are the same connection — every comparison will show no differences.")
    st.caption(f"A = `{_db_label(a)}` · B = `{_db_label(b)}`")

    tabs = ui.lazy_tabs(["One record", "One query", "Row counts"], key="env_tabs")
    if tabs[0].open:
        with tabs[0]:
            _record(a, b)
    if tabs[1].open:
        with tabs[1]:
            _query(a, b)
    if tabs[2].open:
        with tabs[2]:
            _counts(a, b)


def _db_label(name):
    try:
        cfg = db.load_config(name)
        return f"{cfg['database']} @ {cfg['host']}"
    except Exception:
        return name


@st.cache_resource(show_spinner="Reading the structure of the other database…")
def _other_schema(name, version):
    return schema_mod.load(name)


def _schema_of(name):
    if name == ui.active_connection():
        return ui.get_schema()
    return _other_schema(name, st.session_state.get("schema_version", 0))


# ------------------------------------------------------------------ one record
def _record(a, b):
    s = _schema_of(a)
    ui.restore("env_rec_table", "env_rec_col", "env_rec_val")
    names = sorted(s.names)
    if st.session_state.get("env_rec_table") not in names:
        st.session_state.pop("env_rec_table", None)
    x, y, z, w = st.columns([3, 2, 2, 1], vertical_alignment="bottom")
    table = x.selectbox("Table", names, index=None, key="env_rec_table", placeholder="Choose a table",
                        format_func=lambda n: f"{n} · ~{s.rows_est.get(n, 0):,} rows")
    cols = [c for c in s.cols_by_table.get(table or "", []) if not privacy.is_secret_column(c)]
    column = y.selectbox("Column", cols, index=None, key="env_rec_col", placeholder="Key column",
                         format_func=lambda c: ui.indexed_label(c, s.key_of.get((table, c))))
    value = z.text_input("Value", key="env_rec_val")
    go = w.button("Compare", type="primary", key="env_rec_go", disabled=not (table and column and value.strip()))
    ui.remember("env_rec_table", "env_rec_col", "env_rec_val")
    if go:
        sql = ecmp.record_sql(table, column)
        with st.spinner("Reading the record from both databases…"):
            st.session_state.env_rec_res = {
                "table": table, "column": column, "value": value.strip(),
                "a": ui.run_query(sql, [value.strip()], source="env compare", connection=a),
                "b": ui.run_query(sql, [value.strip()], source="env compare", connection=b)}
    res = st.session_state.get("env_rec_res")
    if not res:
        st.info("Pick a table, a key column and a value — for example a campaign id — to see how that record differs "
                "between the two databases.")
        return
    for side, name in (("a", a), ("b", b)):
        if res[side]["error"]:
            st.markdown(f"**{name}**")
            ui.show_error(res[side])
            return
    df_a, df_b = res["a"]["df"], res["b"]["df"]
    if df_a.empty and df_b.empty:
        st.info(f"No row with `{res['column']}` = {evidence.scrub(res['value'])} in either database.")
        return
    if len(df_a) > 1 or len(df_b) > 1:
        st.caption(f"{len(df_a)} rows in A, {len(df_b)} in B — comparing the first of each.")
    row_a = df_a.iloc[0].to_dict() if len(df_a) else None
    row_b = df_b.iloc[0].to_dict() if len(df_b) else None
    fields, missing = ecmp.record_diff(res["table"], res["column"], res["value"], row_a, row_b, store.snapshot_key())
    if missing:
        st.error(f"This record exists only in {'B' if missing == 'left' else 'A'} "
                 f"({b if missing == 'left' else a}).")
    changed = fields[fields["change"].isin(["changed", "added", "removed"])]
    st.markdown(f"**{len(changed)} field{'s' if len(changed) != 1 else ''} differ**" if not missing else "")
    only = st.toggle("Only differing fields", value=True, key="env_rec_only")
    view = (changed if only and not missing else fields).rename(columns={"before": f"in A ({a})", "after": f"in B ({b})"})
    st.dataframe(view, hide_index=True, width="stretch")
    if st.button("Add to evidence", key="env_rec_ev"):
        ui.add_evidence(evidence.make_item(
            "record", f"{res['table']} {res['column']} = {evidence.scrub(res['value'])}: {a} vs {b}",
            df=view, text=f"{len(changed)} field(s) differ between {a} and {b}."))


# ------------------------------------------------------------------ one query
def _query(a, b):
    st.caption("One read-only query, run on both databases. Choose a key column to see which rows are missing on "
               "either side and which fields differ.")
    ui.restore("env_sql")
    sql = st.text_area("SQL (runs on both)", key="env_sql", height=150,
                       placeholder="SELECT id, status, updated_at FROM campaigns ORDER BY id DESC LIMIT 200")
    ui.remember("env_sql")
    if short := sql_explain.summary(sql):
        st.caption(f"In short: {short}")
    x, y, _ = st.columns([1.2, 1.6, 4])
    if x.button("Run on both", type="primary", key="env_sql_go", disabled=not sql.strip()):
        with st.spinner("Running on both databases…"):
            st.session_state.env_sql_res = {
                "sql": sql,
                "a": ui.run_query(sql, source="env compare", connection=a, row_limit=5000),
                "b": ui.run_query(sql, source="env compare", connection=b, row_limit=5000)}
            st.session_state.pop("env_sql_key", None)
    res = st.session_state.get("env_sql_res")
    if not res:
        return
    for side, name in (("a", a), ("b", b)):
        if res[side]["error"]:
            st.markdown(f"**{name}**")
            ui.show_error(res[side])
            return
    df_a, df_b = res["a"]["df"], res["b"]["df"]
    keys = [c for c in ecmp.key_candidates(df_a) if c in df_b.columns]
    m = st.columns(4)
    m[0].metric(f"Rows in A", f"{len(df_a):,}")
    m[1].metric(f"Rows in B", f"{len(df_b):,}", delta=len(df_b) - len(df_a) or None)
    m[2].metric("Seconds A", f"{res['a']['seconds']:.1f}")
    m[3].metric("Seconds B", f"{res['b']['seconds']:.1f}")
    if not keys:
        st.info("No column in this result is unique, so rows cannot be matched one to one. Add an id column to the "
                "query to compare row by row.")
        st.dataframe(privacy.mask(df_a.head(50), ui.is_admin()), hide_index=True, width="stretch")
        return
    key = st.selectbox("Match rows by", keys, key="env_sql_key")
    out = ecmp.compare_results(df_a, df_b, key)
    summ = out["summary"]
    if summ["columns_only_a"] or summ["columns_only_b"]:
        st.warning(f"Different columns — only in A: {summ['columns_only_a'] or '—'}; only in B: {summ['columns_only_b'] or '—'}")
    n = st.columns(3)
    n[0].metric("Only in A", f"{len(out['only_a']):,}")
    n[1].metric("Only in B", f"{len(out['only_b']):,}")
    n[2].metric("Fields that differ", f"{len(out['different']):,}")
    if not len(out["only_a"]) and not len(out["only_b"]) and not len(out["different"]):
        st.success("Both databases returned the same rows and values.")
    for label, frame in ((f"Only in A ({a})", out["only_a"]), (f"Only in B ({b})", out["only_b"]),
                         ("Fields that differ", out["different"])):
        if len(frame):
            with st.expander(f"{label} — {len(frame):,} rows", expanded=label == "Fields that differ"):
                st.dataframe(frame.head(ui.DISPLAY_ROWS), hide_index=True, width="stretch")
    if st.button("Add differences to evidence", key="env_sql_ev", disabled=not len(out["different"])):
        ui.add_evidence(evidence.make_item("query", f"{a} vs {b}: fields that differ", df=out["different"],
                                           sql=res["sql"]))


# ------------------------------------------------------------------ row counts
def _counts(a, b):
    st.caption("Exact COUNT(*) per table on both databases. Counting is slow on very large tables, so pick the ones "
               f"you care about (up to {MAX_COUNT_TABLES}).")
    s_a, s_b = _schema_of(a), _schema_of(b)
    both = sorted(s_a.names | s_b.names)
    est = lambda t: s_a.rows_est.get(t, s_b.rows_est.get(t, 0))
    ui.restore("env_cnt_tables", "env_cnt_mode")
    # Default to tables small enough to count quickly — counting the biggest tables can take minutes.
    st.session_state.setdefault("env_cnt_tables", sorted([t for t in both if 0 < est(t) <= SLOW_ROWS],
                                                         key=lambda t: -est(t))[:10])
    mode = st.radio("How to count", ["Exact COUNT(*)", "Fast estimate (table statistics)"], horizontal=True,
                    key="env_cnt_mode",
                    help="Estimates come from the structure the app already read — instant, but approximate, "
                         "and never exact for InnoDB tables.")
    tables = st.multiselect("Tables", both, key="env_cnt_tables", format_func=lambda t: f"{t} · ~{est(t):,} rows")
    ui.remember("env_cnt_tables", "env_cnt_mode")
    exact = mode.startswith("Exact")
    if len(tables) > MAX_COUNT_TABLES:
        st.warning(f"Only the first {MAX_COUNT_TABLES} tables are counted.")
        tables = tables[:MAX_COUNT_TABLES]
    slow = [t for t in tables if est(t) > SLOW_ROWS]
    if exact and slow:
        st.warning(f"{len(slow)} of these tables have more than {SLOW_ROWS:,} rows — an exact count can take a minute "
                   f"each, or time out. Switch to the fast estimate for those: {', '.join(slow[:5])}"
                   + ("…" if len(slow) > 5 else ""))
    if st.button("Count on both", type="primary", key="env_cnt_go", disabled=not tables):
        if not exact:
            st.session_state.env_cnt_res = {"tables": tables, "exact": False,
                                            "a": {t: (s_a.rows_est.get(t) if t in s_a.names else None) for t in tables},
                                            "b": {t: (s_b.rows_est.get(t) if t in s_b.names else None) for t in tables}}
        else:
            with st.spinner(f"Counting {len(tables)} tables on both databases…"):
                counts = {}
                for side, conn, s_ in (("a", a, s_a), ("b", b, s_b)):
                    got = ui.run_many([(t, ecmp.count_sql(t), None) for t in tables if t in s_.names],
                                      source="env compare", connection=conn)
                    counts[side] = {t: (None if res["error"] else int(res["df"].iloc[0]["rows"])) for t, res in got}
                    counts[side].update({t: None for t in tables if t not in s_.names})
                st.session_state.env_cnt_res = {"tables": tables, "exact": True, **counts}
    res = st.session_state.get("env_cnt_res")
    if not res:
        return
    table = ecmp.count_table(res["a"], res["b"], res["tables"])
    same = int((table["status"] == "same").sum())
    st.caption(f"{same} of {len(table)} tables have the same number of rows." +
               ("" if res.get("exact", True) else " Estimates only — small differences here mean nothing."))
    st.dataframe(table.rename(columns={"rows in A": f"rows in A ({a})", "rows in B": f"rows in B ({b})"}),
                 hide_index=True, width="stretch", height=min(520, 38 + 35 * len(table)))
    x, y, _ = st.columns([1.3, 1.3, 4])
    x.download_button("Download CSV", table.to_csv(index=False).encode("utf-8"), file_name=f"row_counts_{a}_vs_{b}.csv",
                      mime="text/csv", on_click="ignore")
    if y.button("Add to evidence", key="env_cnt_ev"):
        ui.add_evidence(evidence.make_item("note", f"Row counts: {a} vs {b}", df=table,
                                           text=f"{same} of {len(table)} tables match."))
