# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Charts: ready-made views of the data, plus charts from your own saved queries."""
import streamlit as st

import auth
import db
import packs
import ui


def render():
    st.title("Charts")
    if not ui.require_connection():
        return
    dbname = db.database_name(ui.active_connection())
    available = [c for c in packs.load_list("charts") if c.get("database") in (None, dbname)]

    tab_ready, tab_mine = ui.lazy_tabs(["Ready-made charts", "Chart a saved query"], key="ch_tabs")
    if tab_ready.open:
        with tab_ready:
            _ready_charts(available)
    if not tab_mine.open:
        return
    with tab_mine:
        saved = auth.saved_queries(ui.user()["id"])
        if not saved:
            st.info("Save a query in the SQL editor, then chart it here.")
            return
        labels = {q["id"]: f"{q['title']}  ·  by {q['owner']}" for q in saved}
        qid = st.selectbox("Saved query", list(labels), format_func=labels.get, key="ch_saved")
        q = next(x for x in saved if x["id"] == qid)
        if st.button("Run and chart", type="primary", key="ch_saved_run"):
            st.session_state.ch_saved_res = ui.run_query(q["sql"], source="charts", row_limit=20000)
        res = st.session_state.get("ch_saved_res")
        if res:
            ui.show_result(res, key="ch_saved_out", chart=True)


def _ready_charts(available):
    if not available:
        st.info("No ready-made charts for this database yet. Add one to local/charts.yaml.")
        return
    a, b = st.columns([5, 1], vertical_alignment="bottom")
    pick = a.selectbox("Chart", [c["title"] for c in available], key="ch_pick")
    if b.button("Refresh data", help="Charts reuse results for 10 minutes"):
        ui._cached.clear()
    spec = next(c for c in available if c["title"] == pick)
    if spec.get("note"):
        st.caption(spec["note"])
    with st.spinner("Loading…"):
        res = ui.cached_query(spec["sql"])
    if res["error"]:
        st.error(res["error"])
        return
    if res["df"].empty:
        st.info("No data for this chart yet.")
        return
    data = res["df"].copy()
    missing = [c for c in (spec.get("x"), spec.get("y"), spec.get("color")) if c and c not in data.columns]
    if missing:
        st.error(f"The chart settings name columns the query does not return: {', '.join(missing)}.")
        return
    if spec["kind"] in ("Bar", "Pie"):
        data[spec["x"]] = data[spec["x"]].astype(str)
    fig = ui.draw(data, spec["kind"], spec["x"], spec["y"], spec.get("color"))
    st.plotly_chart(fig, width="stretch", key="ch_fig")
    with st.expander("Data and SQL behind this chart"):
        st.code(spec["sql"].strip(), language="sql")
        ui.show_result({**res, "ran_at": None}, key="ch_data", chart=False)
