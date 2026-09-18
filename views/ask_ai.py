"""Ask in plain words: Gemini writes the SQL, you check it, then run it read-only."""
import streamlit as st

import ai
import ui

MAX_TABLES = 15


def render():
    st.title("Ask AI")
    st.caption("Describe what you need in plain words. AI writes the SQL; you review it before it runs. "
               "Only table and column names go to Gemini — never any data from the database.")
    if not ui.require_connection():
        return
    if not ai.configured():
        if ui.is_admin():
            st.info("Add a Gemini API key on the Admin page to turn this on. A free key from "
                    "Google AI Studio is enough.")
            if st.button("Go to Admin"):
                ui.go("admin")
        else:
            st.info("AI is not turned on yet. Ask an admin to add a Gemini API key.")
        return

    s = ui.get_schema()
    ui.apply_pending("ai_sql")
    ui.apply_pending("ai_tables")
    if st.session_state.get("ai_tables"):
        st.session_state.ai_tables = [t for t in st.session_state.ai_tables if t in s.names]

    question = st.text_area("Your question", key="ai_q", height=90,
                            placeholder="e.g. How many users signed up each day last week? "
                                        "or: list orders over 5,000 with the customer name")

    a, b = st.columns([5, 2], vertical_alignment="bottom")
    tables = a.multiselect(f"Tables to use (up to {MAX_TABLES}; leave empty and AI picks)",
                           sorted(s.names), key="ai_tables", max_selections=MAX_TABLES)
    if b.button("Suggest tables", disabled=not question, help="Ask AI which tables fit the question"):
        with st.spinner("Finding tables…"):
            try:
                picks = ai.suggest_tables(question, s)
                ui.set_pending("ai_tables", [p["table"] for p in picks])
                st.session_state.ai_why = picks
                st.rerun()
            except ai.AIError as e:
                st.error(str(e))
    if st.session_state.get("ai_why"):
        with st.expander("Why these tables"):
            for p in st.session_state.ai_why:
                st.markdown(f"- **{p['table']}** — {p.get('why', '')}")

    if st.button("Write the SQL", type="primary", disabled=not question):
        use = tables
        with st.spinner("Writing SQL…"):
            try:
                if not use:
                    picks = ai.suggest_tables(question, s)
                    use = [p["table"] for p in picks] or s.find_tables(question).head(8)["name"].tolist()
                    st.session_state.ai_why = picks
                    ui.set_pending("ai_tables", use[:MAX_TABLES])
                out = ai.write_sql(question, use[:MAX_TABLES], s)
                st.session_state.ai_out = out
                st.session_state.ai_result = None
                ui.set_pending("ai_sql", out["sql"])
                st.rerun()
            except ai.AIError as e:
                st.error(str(e))

    out = st.session_state.get("ai_out")
    if not out and not st.session_state.get("ai_sql"):
        return

    if out:
        with st.container(border=True):
            st.markdown(f"**What this query does:** {out.get('explanation') or '—'}")
            if out.get("assumptions"):
                st.markdown(f"**Assumptions to check:** {out['assumptions']}")

    st.text_area("SQL (edit it if needed)", key="ai_sql", height=200)
    x, y, z, _ = st.columns([1, 1.3, 1.6, 3])
    if x.button("Run", type="primary", key="ai_run"):
        with st.spinner("Running…"):
            st.session_state.ai_result = ui.run_query(st.session_state.ai_sql, source="ask ai", row_limit=1000)
    if y.button("Explain simply", key="ai_explain"):
        with st.spinner("Explaining…"):
            try:
                st.session_state.ai_explained = ai.explain_sql(
                    st.session_state.ai_sql, s, ai.tables_in_sql(st.session_state.ai_sql, s))
            except ai.AIError as e:
                st.error(str(e))
    if z.button("Open in SQL editor", key="ai_to_sql"):
        ui.set_pending("sql_text", st.session_state.ai_sql)
        ui.go("sql")

    if st.session_state.get("ai_explained"):
        with st.expander("Explanation", expanded=True):
            st.markdown(st.session_state.ai_explained)

    res = st.session_state.get("ai_result")
    if res:
        ui.show_result(res, key="ai_result")
        if res["error"] and st.button("Ask AI to fix this error", key="ai_fix"):
            with st.spinner("Fixing…"):
                try:
                    used = ai.tables_in_sql(st.session_state.ai_sql, s) or list(tables)
                    fixed = ai.fix_sql(question, st.session_state.ai_sql, res["error"], used[:MAX_TABLES], s)
                    st.session_state.ai_out = fixed
                    st.session_state.ai_result = None
                    ui.set_pending("ai_sql", fixed["sql"])
                    st.rerun()
                except ai.AIError as e:
                    st.error(str(e))
