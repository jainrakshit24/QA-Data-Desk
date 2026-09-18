# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""SQL editor with a table helper, saved queries and history."""
import pandas as pd
import streamlit as st

import ai
import auth
import db
import privacy
import ui
from core import params as sql_params
from core import performance


def render():
    st.title("SQL editor")
    st.caption("Read-only: SELECT, SHOW, DESCRIBE, EXPLAIN and WITH queries only. Queries stop after 60 seconds.")
    if not ui.require_connection():
        return
    s = ui.get_schema()
    u = ui.user()
    ui.apply_pending("sql_text")

    main, helper = st.columns([3, 1.4])
    with helper:
        with st.container(border=True):
            st.markdown("**Table helper**")
            term = st.text_input("Find a table", key="sq_help_term", placeholder="type part of a name")
            found = s.find_tables(term, limit=30)["name"].tolist() if term else []
            if found:
                t = st.selectbox("Table", found, key="sq_help_table")
                cols = s.table_columns(t)
                safe = [c for c in cols["col"] if not privacy.is_secret_column(c)]
                st.dataframe(cols[~cols["col"].map(privacy.is_secret_column)][["col", "type", "key"]]
                             .rename(columns={"col": "column", "key": "idx"}),
                             width="stretch", hide_index=True, height=260)
                if st.button("Insert SELECT for this table", key="sq_insert"):
                    ui.set_pending("sql_text", f"SELECT {', '.join(safe)}\nFROM {t}\nLIMIT 100")
                    st.rerun()

    with main:
        st.text_area("Query", key="sql_text", height=280,
                     placeholder="SELECT id, name, status FROM orders WHERE created_at >= CURDATE() - INTERVAL 7 DAY LIMIT 100")
        sql = st.session_state.get("sql_text", "")
        names = sql_params.names(sql)
        values = {}
        if names:
            st.caption("Parameters — values are bound safely, never pasted into the SQL")
            pcols = st.columns(min(len(names), 4))
            for i, n in enumerate(names):
                ui.apply_pending(f"sq_param_{n}")
                values[n] = pcols[i % len(pcols)].text_input(f":{n}", key=f"sq_param_{n}")
        missing = [n for n in names if not str(values.get(n, "")).strip()]
        a, b, c, e, d = st.columns([1, 1.4, 1.1, 1.1, 2.2], vertical_alignment="bottom")
        limit = d.selectbox("Rows if no LIMIT", [100, 1000, 5000, 20000], index=1, key="sq_limit",
                            help="Queries without their own LIMIT stop at this many rows")
        autorun = st.session_state.pop("sq_autorun", False) and sql.strip() and not missing
        if a.button("Run", type="primary", disabled=not sql.strip() or bool(missing),
                    help=f"Fill in :{missing[0]} first" if missing else None) or autorun:
            bound_sql, bound = sql_params.bind(sql, {k: v.strip() for k, v in values.items()})
            with st.spinner("Running…"):
                st.session_state.sq_result = ui.run_query(bound_sql, bound or None, source="sql editor", row_limit=limit)
            loaded = st.session_state.get("sq_loaded")
            if loaded and loaded["sql"].strip() == sql.strip():
                auth.mark_query_run(loaded["id"])
        if e.button("Analyze", disabled=not sql.strip() or bool(missing),
                    help="Show how MySQL would run this query (EXPLAIN). The query itself is not run."):
            bound_sql, bound = sql_params.bind(sql, {k: v.strip() for k, v in values.items()})
            st.session_state.sq_plan = _analyze(bound_sql, bound)
        if ai.configured() and b.button("Explain simply", disabled=not sql.strip()):
            with st.spinner("Asking AI…"):
                try:
                    st.session_state.sq_explained = ai.explain_sql(sql, s, ai.tables_in_sql(sql, s))
                except ai.AIError as e:
                    st.error(str(e))
        with c.popover("Save", disabled=not sql.strip()):
            _save_form(u, sql)

        if st.session_state.get("sq_plan"):
            _show_plan(st.session_state.sq_plan)
        if st.session_state.get("sq_explained"):
            with st.expander("Explanation", expanded=True):
                st.markdown(st.session_state.sq_explained)
        res = st.session_state.get("sq_result")
        if res:
            ui.show_result(res, key="sql_result")
            if res["error"] and ai.configured() and st.button("Ask AI to fix this error"):
                with st.spinner("Fixing…"):
                    try:
                        fixed = ai.fix_sql("", sql, res["error"], ai.tables_in_sql(sql, s)[:15], s)
                        ui.set_pending("sql_text", fixed["sql"])
                        st.session_state.sq_result = None
                        st.rerun()
                    except ai.AIError as e:
                        st.error(str(e))

    st.divider()
    tab_lib, tab_hist = ui.lazy_tabs(["Query library", "History"], key="sq_tabs")
    if tab_lib.open:
        with tab_lib:
            _library(u, s)
    if tab_hist.open:
        with tab_hist:
            _history(u)


def _load(sql, query_id=None, run=False):
    ui.set_pending("sql_text", sql)
    st.session_state.sq_loaded = {"id": query_id, "sql": sql} if query_id else None
    st.session_state.sq_autorun = run
    st.session_state.sq_result = None
    st.rerun()


def _save_form(u, sql, prefix="sq_save", title_default=""):
    with st.form(prefix, clear_on_submit=True):
        title = st.text_input("Title", value=title_default)
        notes = st.text_area("Notes (optional)", height=70)
        a, b = st.columns(2)
        tags = a.text_input("Tags", placeholder="payments, leads, debugging")
        category = b.text_input("Category", placeholder="Investigation")
        shared = st.checkbox("Share with the team")
        if st.form_submit_button("Save query", type="primary"):
            try:
                db.check_read_only(sql)
                privacy.check_sql(sql)
                if not title.strip():
                    raise ValueError("Give the query a title.")
                auth.save_query(u["id"], title, sql, notes, shared, tags, category)
                st.success("Saved to the query library.")
            except (ValueError, privacy.PrivacyError) as e:
                st.error(str(e))


def _library(u, s):
    saved = auth.saved_queries(u["id"])
    if not saved:
        st.caption("Nothing saved yet. Write a query and use Save.")
        return
    ui.restore("sq_lib_view", "sq_lib_term", "sq_lib_tags")
    a, b, c = st.columns([3, 2, 2])
    view = a.segmented_control("Show", ["My queries", "Team queries", "Popular", "Recently used"],
                               default="My queries", key="sq_lib_view")
    term = b.text_input("Search", key="sq_lib_term", placeholder="title, SQL, notes")
    all_tags = sorted({t for q in saved for t in q["tag_list"]})
    tags = c.multiselect("Tags", all_tags, key="sq_lib_tags")
    ui.remember("sq_lib_view", "sq_lib_term", "sq_lib_tags")

    items = saved
    if view == "My queries":
        items = [q for q in saved if q["user_id"] == u["id"]]
    elif view == "Team queries":
        items = [q for q in saved if q["shared"]]
    elif view == "Popular":
        items = sorted([q for q in saved if q["execution_count"]], key=lambda q: -q["execution_count"])
    elif view == "Recently used":
        items = sorted([q for q in saved if q["last_executed"]], key=lambda q: q["last_executed"], reverse=True)
    if term:
        items = [q for q in items if term.lower() in f"{q['title']} {q['sql']} {q['notes'] or ''} {q['category']}".lower()]
    if tags:
        items = [q for q in items if set(tags) & set(q["tag_list"])]
    if not items:
        st.caption("No queries match.")
    for q in items[:100]:
        runs = f"{q['execution_count']} run" + ("" if q["execution_count"] == 1 else "s")
        with st.expander(f"{q['title']}  ·  {q['owner']}{'  ·  shared' if q['shared'] else ''}  ·  {runs}"):
            meta = [f"created by {q['owner']}", f"modified {q['updated_at'][:16].replace('T', ' ')}"]
            if q["last_executed"]:
                meta.append(f"last run {q['last_executed'][:16].replace('T', ' ')}")
            if q["category"]:
                meta.append(f"category: {q['category']}")
            if q["tag_list"]:
                meta.append("tags: " + ", ".join(q["tag_list"]))
            st.caption(" · ".join(meta))
            if q["notes"]:
                st.markdown(q["notes"])
            st.code(q["sql"], language="sql")
            x, y, z, w, v, _ = st.columns([0.8, 0.8, 1.2, 0.8, 0.9, 2])
            if x.button("Load", key=f"sq_load_{q['id']}"):
                _load(q["sql"], q["id"])
            if y.button("Run", key=f"sq_runq_{q['id']}", help="Load and run now (fill in any :parameters first)"):
                _load(q["sql"], q["id"], run=True)
            if ai.configured() and z.button("Explain simply", key=f"sq_expq_{q['id']}"):
                with st.spinner("Asking AI…"):
                    try:
                        st.session_state[f"sq_expl_{q['id']}"] = ai.explain_sql(q["sql"], s, ai.tables_in_sql(q["sql"], s))
                    except ai.AIError as e:
                        st.error(str(e))
            mine = q["user_id"] == u["id"] or ui.is_admin()
            if mine:
                with w.popover("Edit"):
                    with st.form(f"sq_edit_{q['id']}"):
                        title = st.text_input("Title", q["title"])
                        tags_in = st.text_input("Tags", ", ".join(q["tag_list"]))
                        category = st.text_input("Category", q["category"])
                        notes = st.text_area("Notes", q["notes"] or "", height=70)
                        shared = st.checkbox("Share with the team", bool(q["shared"]))
                        if st.form_submit_button("Save changes", type="primary"):
                            auth.update_query(q["id"], u["id"], ui.is_admin(), title=title, tags=tags_in,
                                              category=category, notes=notes, shared=shared)
                            st.rerun()
                if v.button("Delete", key=f"sq_del_{q['id']}"):
                    auth.delete_query(q["id"], u["id"], ui.is_admin())
                    st.rerun()
            if st.session_state.get(f"sq_expl_{q['id']}"):
                st.info(st.session_state[f"sq_expl_{q['id']}"])


def _history(u):
    hist = auth.recent_log(u["id"], limit=300)
    if not hist:
        st.caption("Queries you run will appear here.")
        return
    df = pd.DataFrame(hist)
    df["database"] = df["connection"].fillna("—") if "connection" in df else "—"
    df["status"] = df["error"].map(lambda e: "failed" if e else "ok")
    ui.restore("sq_hist_db", "sq_hist_status", "sq_hist_term")
    a, b, c = st.columns([2, 2, 3])
    dbs = sorted(df["database"].unique())
    pick_db = a.selectbox("Database", ["All"] + dbs, key="sq_hist_db")
    status = b.segmented_control("Result", ["All", "ok", "failed"], default="All", key="sq_hist_status")
    term = c.text_input("SQL contains", key="sq_hist_term")
    ui.remember("sq_hist_db", "sq_hist_status", "sq_hist_term")
    view = df
    if pick_db != "All":
        view = view[view["database"] == pick_db]
    if status in ("ok", "failed"):
        view = view[view["status"] == status]
    if term:
        view = view[view["sql"].str.contains(term, case=False, regex=False)]
    st.caption("Emails and values compared with phone or secret columns are masked in history — re-enter them before re-running.")
    event = st.dataframe(view[["ran_at", "database", "source", "status", "rows", "seconds", "sql", "error"]],
                         width="stretch", hide_index=True, height=320, on_select="rerun", selection_mode="single-row",
                         key="sq_hist_grid")
    rows = event.selection.rows if event and event.selection else []
    if not rows:
        st.caption("Select a row to load, re-run or save it.")
        return
    chosen = view.iloc[rows[0]]
    st.code(chosen["sql"], language="sql")
    x, y, z, _ = st.columns([1, 1.2, 1.4, 4])
    if x.button("Load", key="sq_hist_load"):
        _load(chosen["sql"])
    if y.button("Run again", key="sq_hist_run"):
        _load(chosen["sql"], run=True)
    with z.popover("Save as query"):
        _save_form(u, chosen["sql"], prefix="sq_hist_save")


def _analyze(sql, params):
    first = (sql or "").lstrip("( \n\t").split(None, 1)[0].lower() if sql.strip() else ""
    if first not in ("select", "with"):
        return {"error": "Only SELECT and WITH queries can be analyzed.", "sql": sql}
    res = ui.run_query("EXPLAIN FORMAT=JSON " + sql, params or None, source="query analyzer")
    if res["error"]:
        return {"error": res["error"], "sql": sql}
    try:
        plan = performance.parse_plan(res["df"].iloc[0, 0])
    except (ValueError, KeyError, IndexError) as e:
        return {"error": f"Could not read the plan: {e}", "sql": sql}
    warnings, suggestions = performance.assess(plan, sql)
    return {"error": None, "plan": plan, "warnings": warnings, "suggestions": suggestions, "sql": sql}


def _show_plan(result):
    with st.container(border=True):
        top = st.columns([3, 1])
        top[0].markdown("**Query analysis** — how MySQL plans to run it. Nothing was executed.")
        if top[1].button("Close", key="sq_plan_close", type="tertiary"):
            st.session_state.sq_plan = None
            st.rerun()
        if result["error"]:
            st.error(result["error"])
            return
        plan = result["plan"]
        m = st.columns(3)
        m[0].metric("Estimated cost", f"{plan['query_cost']:,.0f}" if plan["query_cost"] is not None else "—")
        m[1].metric("Most rows read from one table", f"{max([t['rows examined'] or 0 for t in plan['tables']] or [0]):,}")
        m[2].metric("Full table scans", sum(1 for t in plan["tables"] if t["access"] == "ALL"))
        icon = {"high": "🔴", "medium": "🟠", "low": "🟡"}
        for level, msg in result["warnings"]:
            st.markdown(f"{icon[level]} {msg}")
        if result["suggestions"]:
            st.markdown("**Suggestions**\n" + "\n".join(f"- {s}" for s in result["suggestions"]))
        if plan["tables"]:
            st.dataframe(pd.DataFrame(plan["tables"])[["table", "how", "index used", "rows examined", "filtered %", "meaning"]],
                         hide_index=True, width="stretch")
