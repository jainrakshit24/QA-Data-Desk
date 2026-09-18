# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Search everything: tables, columns, saved queries, QA checks, playbooks, consistency rules and history."""
import streamlit as st

import auth
import qa_checks
import store
import ui
from core import global_search
from views import consistency as consistency_view
from views import playbooks as playbooks_view

ICON = {"Table": "🗂", "Column": "🔤", "Saved query": "💾", "QA check": "✅", "Playbook": "📘",
        "Consistency rule": "⚖️", "History": "🕘"}


def render():
    st.title("Search")
    if not ui.require_connection():
        return
    s = ui.get_schema()
    u = ui.user()
    ui.apply_pending("gs_term")
    term = st.text_input("Search everything", key="gs_term",
                         placeholder="payment, lead_id, duplicate users, a query title, a check name…")
    kinds = st.pills("Show", global_search.KINDS, selection_mode="multi", default=global_search.KINDS, key="gs_kinds")
    if len(term.strip()) < 2:
        st.caption("Type at least two characters. Searches table and column names, your saved and shared queries, "
                   "QA checks, playbooks, consistency rules and your query history.")
        return
    try:
        checks = qa_checks.load(qa_checks.database_name())
    except Exception:
        checks = qa_checks.load()
    results = global_search.search(
        term, schema=s, saved_queries=auth.saved_queries(u["id"]), checks=checks,
        playbooks=playbooks_view._all(u), rules=store.rules(u["id"]) + consistency_view._local_rules(),
        history=auth.recent_log(u["id"], limit=500))
    results = [r for r in results if r["kind"] in (kinds or [])]
    if not results:
        st.info(f"Nothing matches “{term}”.")
        return
    counts = {k: sum(1 for r in results if r["kind"] == k) for k in global_search.KINDS}
    st.caption(" · ".join(f"{ICON[k]} {k}: {n}" for k, n in counts.items() if n))
    current = None
    for i, r in enumerate(results):
        if r["kind"] != current:
            current = r["kind"]
            st.subheader(f"{ICON[current]} {current}")
        a, b = st.columns([6, 1], vertical_alignment="center")
        a.markdown(f"**{r['title']}**  \n{r['detail']}")
        if b.button("Open", key=f"gs_open_{i}"):
            _open(r)


def _open(r):
    t = r["target"]
    kind = r["kind"]
    if kind == "Table":
        ui.set_pending("ex_mode", "Table names")
        ui.set_pending("ex_term", t["table"])
        st.session_state.ex_table = t["table"]
        ui.go("find")
    elif kind == "Column":
        ui.set_pending("ex_mode", "Column names")
        ui.set_pending("ex_term", t["column"])
        st.session_state.ex_table = t["table"]
        st.session_state.ex_last_term = t["column"]
        ui.go("find")
    elif kind in ("Saved query", "History"):
        ui.set_pending("sql_text", t["sql"])
        st.session_state.sq_loaded = {"id": t["query_id"], "sql": t["sql"]} if t.get("query_id") else None
        st.session_state.sq_result = None
        ui.go("sql")
    elif kind == "QA check":
        st.session_state.pop("_keep_chk_term", None)
        ui.set_pending("chk_term", t["title"])
        ui.go("checks")
    elif kind == "Playbook":
        st.session_state.pop("_keep_pb_pick", None)
        st.session_state["_pending_pb_pick"] = t["playbook_key"]
        ui.go("playbooks")
    elif kind == "Consistency rule":
        st.session_state.pop("_keep_cons_pick", None)
        st.session_state["_pending_cons_pick"] = t["rule_id"]
        ui.go("consistency")
