"""Test ideas: edge cases for a table, and test data suggestions. Nothing here writes to the database."""
import pandas as pd
import streamlit as st

import ai
import ui
from core import edge_cases as ec
from core import evidence
from core import test_data as td


def render():
    st.title("Test ideas")
    st.caption("Edge cases and test data for any table, worked out from its columns. Suggestions only — "
               "this app never writes to the database.")
    if not ui.require_connection():
        return
    s = ui.get_schema()
    ui.apply_pending("ti_table")
    ui.restore("ti_table")
    names = sorted(s.names)
    if st.session_state.get("ti_table") not in names:
        st.session_state.pop("ti_table", None)
    table = st.selectbox("Table", names, index=None, key="ti_table", placeholder="Type a table name…",
                         format_func=lambda n: f"{n}   ·   ~{s.rows_est.get(n, 0):,} rows")
    ui.remember("ti_table")
    if not table:
        st.info("Pick a table. You get edge cases per column, a count of how many existing rows already hit them, "
                "and example test data you can copy.")
        return
    tabs = ui.lazy_tabs(["Edge cases", "Test data"], key="ti_tabs")
    if tabs[0].open:
        with tabs[0]:
            _edge_cases(s, table)
    if tabs[1].open:
        with tabs[1]:
            _test_data(s, table)


def _edge_cases(s, table):
    cases = pd.DataFrame(ec.for_table(s, table), columns=["column", "category", "case", "example", "why"])
    if cases.empty:
        st.info("No edge cases to suggest for this table.")
        return
    a, b = st.columns([2, 3])
    cats = a.multiselect("Category", [c for c in ec.CATEGORIES if c in set(cases["category"])], key="ti_cats")
    cols = b.multiselect("Columns", list(dict.fromkeys(cases["column"])), key="ti_cols")
    view = cases
    if cats:
        view = view[view["category"].isin(cats)]
    if cols:
        view = view[view["column"].isin(cols)]
    st.caption(f"{len(view):,} of {len(cases):,} ideas · secret columns are skipped")
    st.dataframe(view, hide_index=True, width="stretch", height=min(560, 38 + 35 * max(1, len(view))),
                 column_config={"why": st.column_config.TextColumn(width="large")})
    x, y, _ = st.columns([1.3, 1.3, 4])
    x.download_button("Download CSV", view.to_csv(index=False).encode("utf-8"), file_name=f"edge_cases_{table}.csv",
                      mime="text/csv", on_click="ignore")
    if y.button("Add to evidence", key="ti_ev"):
        ui.add_evidence(evidence.make_item("note", f"Edge cases for {table}", df=view))
        st.toast("Added to evidence.")

    st.subheader("Do existing rows already hit these cases?")
    sql, used = ec.coverage_sql(s, table)
    st.caption("Counts NULLs, empty strings, extra spaces, longest text, zero / negative numbers and date ranges in "
               "the newest 5,000 rows. Only counts — personal columns never show a value.")
    with st.popover("Show SQL"):
        st.code(sql, language="sql")
    if st.button("Count in the database", key="ti_cov", type="primary"):
        with st.spinner("Counting…"):
            st.session_state.ti_cov_res = {"table": table, "res": ui.run_query(sql, source="test ideas")}
    got = st.session_state.get("ti_cov_res")
    if got and got["table"] == table:
        res = got["res"]
        if res["error"]:
            ui.show_error(res)
        elif res["df"] is not None and len(res["df"]):
            total, rows = ec.coverage_table(res["df"].iloc[0].to_dict(), used, s, table)
            st.caption(f"{int(total or 0):,} rows sampled · {res['seconds']:.1f}s")
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _test_data(s, table):
    st.warning("Generated SQL is **NOT EXECUTED**. This app is read-only; copy it into a test environment you are "
               "allowed to change. Prefer reusing existing data (below) where you can.")
    a, b = st.columns([3, 1])
    variant = a.segmented_control("Rows", list(td.VARIANTS), format_func=td.VARIANTS.get, default="valid", key="ti_variant")
    count = b.number_input("How many", 1, 20, 3, key="ti_count")
    rows, notes = td.sample_rows(s, table, variant or "valid", int(count))
    fmt = st.radio("Format", ["SQL", "JSON"], horizontal=True, key="ti_fmt", label_visibility="collapsed")
    st.code(td.insert_sql(table, rows) if fmt == "SQL" else td.as_json(rows), language="sql" if fmt == "SQL" else "json")
    for n in notes:
        st.caption("• " + n)

    st.subheader("Find existing data instead")
    st.caption("Read-only queries for rows already in useful states. Open one in the SQL editor to run it.")
    for i, q in enumerate(td.find_existing(s, table)):
        c1, c2 = st.columns([6, 1.3], vertical_alignment="center")
        c1.markdown(f"**{q['title']}**")
        if c2.button("Open in SQL editor", key=f"ti_find_{i}"):
            ui.set_pending("sql_text", q["sql"])
            ui.go("sql")

    st.subheader("Ask AI for scenarios")
    if not ai.configured():
        st.caption("Add a Gemini key on the Admin page to get scenario ideas. Only table and column names are sent.")
        return
    goal = st.text_input("What do you want to test?", key="ti_goal",
                         placeholder="e.g. campaign that is paused but still has active delivery units")
    if st.button("Suggest scenarios", key="ti_ai", disabled=not goal.strip()):
        with st.spinner("Asking Gemini…"):
            try:
                st.session_state.ti_ai_res = {"table": table, "items": ai.test_data_ideas(evidence.scrub(goal), table, s)}
            except ai.AIError as e:
                st.error(str(e))
    got = st.session_state.get("ti_ai_res")
    if got and got["table"] == table:
        st.caption("AI suggestions — check them before use. Nothing was run.")
        for i, sc in enumerate(got["items"]):
            with st.expander(sc["name"]):
                st.write(sc["why"])
                if sc["row"]:
                    st.code(td.insert_sql(table, [sc["row"]]), language="sql")
                if sc["find_sql"]:
                    st.code(sc["find_sql"], language="sql")
                    if st.button("Open find query in SQL editor", key=f"ti_ai_find_{i}"):
                        ui.set_pending("sql_text", sc["find_sql"])
                        ui.go("sql")
