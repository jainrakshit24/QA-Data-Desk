# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""QA Data Desk — read-only database explorer with sign-in.

Run:  ./run.sh     (or: python3 -m streamlit run app.py)
"""
import streamlit as st

import envfile  # noqa: E402

envfile.load()

st.set_page_config(page_title="QA Data Desk", page_icon="🔎", layout="wide")

import logging  # noqa: E402
# Widgets inside lazy tabs are restored through Session State on purpose (ui.restore); this log line is expected.
logging.getLogger("streamlit.elements.lib.policies").setLevel(logging.ERROR)

import auth  # noqa: E402
import db  # noqa: E402
import store  # noqa: E402
import ui  # noqa: E402
from views import (account, admin, ask_ai, charts, checks, consistency, dashboard, evidence_page,  # noqa: E402
                   explorer, investigate, login, lookup, playbooks, record, schema_compare, search, sql_editor,
                   test_ideas, validate, health, env_compare, databases, legal)

auth.init()
store.init()
store.apply_privacy_settings()


@st.cache_resource
def _announce_setup_code():
    if auth.user_count() == 0:
        print(f"\n  QA Data Desk first-run setup code: {auth.setup_code()}\n"
              "  Enter it on the sign-in page to create the admin account.\n", flush=True)
    return True


_announce_setup_code()

# Re-read the signed-in person every run, so a disabled account is signed out at once.
u = st.session_state.get("user")
if u:
    fresh = auth.get_user(u["id"])
    if not fresh or fresh["status"] != "active":
        st.session_state.clear()
        u = None
    else:
        u = {k: fresh[k] for k in ("id", "username", "full_name", "email", "role", "status")}
        st.session_state.user = u

if not u:
    public = {
        "login": st.Page(login.render, title="Sign in", icon=":material/login:", default=True, url_path="sign-in"),
        "privacy": st.Page(legal.privacy, title="Privacy policy", icon=":material/privacy_tip:", url_path="privacy"),
        "terms": st.Page(legal.terms, title="Terms of use", icon=":material/gavel:", url_path="terms"),
    }
    ui.PAGES.update(public)
    signed_out = st.navigation(list(public.values()), position="hidden")
    ui.remember_current_page(public, signed_out)
    ui.insecure_warning()
    ui.cookie_notice()
    signed_out.run()
    st.stop()

evidence_count = len(st.session_state.get("evidence", []))
pages = {
    "investigate": st.Page(investigate.render, title="Start investigation", icon=":material/manage_search:",
                           url_path="investigate", default=True),
    "record": st.Page(record.render, title="Investigate record", icon=":material/account_tree:", url_path="record"),
    "validate": st.Page(validate.render, title="Validate API", icon=":material/rule:", url_path="validate"),
    "consistency": st.Page(consistency.render, title="Data consistency", icon=":material/compare_arrows:",
                           url_path="consistency"),
    "schema": st.Page(schema_compare.render, title="Schema compare", icon=":material/difference:", url_path="schema"),
    "playbooks": st.Page(playbooks.render, title="Playbooks", icon=":material/menu_book:", url_path="playbooks"),
    "dashboard": st.Page(dashboard.render, title="QA dashboard", icon=":material/monitor_heart:", url_path="dashboard"),
    "evidence": st.Page(evidence_page.render, title=f"Bug evidence ({evidence_count})" if evidence_count else "Bug evidence",
                        icon=":material/assignment:", url_path="evidence"),
    "envs": st.Page(env_compare.render, title="Compare environments", icon=":material/compare_arrows:", url_path="environments"),
    "health": st.Page(health.render, title="Database health", icon=":material/monitor_heart:", url_path="health"),
    "tests": st.Page(test_ideas.render, title="Test ideas", icon=":material/lightbulb:", url_path="test-ideas"),
    "search": st.Page(search.render, title="Search everything", icon=":material/search:", url_path="search"),
    "find": st.Page(explorer.render, title="Find data", icon=":material/travel_explore:", url_path="find"),
    "lookup": st.Page(lookup.render, title="Find by ID", icon=":material/tag:", url_path="find-by-id"),
    "ai": st.Page(ask_ai.render, title="Ask AI", icon=":material/auto_awesome:", url_path="ask-ai"),
    "checks": st.Page(checks.render, title="QA checks", icon=":material/fact_check:", url_path="checks"),
    "charts": st.Page(charts.render, title="Charts", icon=":material/bar_chart:", url_path="charts"),
    "sql": st.Page(sql_editor.render, title="SQL editor", icon=":material/code:", url_path="sql"),
    "account": st.Page(account.render, title="My account", icon=":material/person:", url_path="account"),
    "databases": st.Page(databases.render, title="Databases", icon=":material/database:", url_path="databases"),
    "privacy": st.Page(legal.privacy, title="Privacy policy", icon=":material/privacy_tip:", url_path="privacy"),
    "terms": st.Page(legal.terms, title="Terms of use", icon=":material/gavel:", url_path="terms"),
}
if u["role"] == "admin":
    pages["admin"] = st.Page(admin.render, title="Admin", icon=":material/admin_panel_settings:", url_path="admin")
ui.PAGES.update(pages)

nav = {
    "Investigate": [pages["investigate"], pages["record"], pages["playbooks"], pages["evidence"]],
    "Validate": [pages["validate"], pages["consistency"], pages["schema"], pages["envs"], pages["tests"]],
    "Monitor": [pages["dashboard"], pages["checks"], pages["health"]],
    "Explore": [pages["search"], pages["find"], pages["lookup"], pages["ai"], pages["charts"], pages["sql"]],
    "Settings": [pages["account"], pages["databases"]] + ([pages["admin"]] if "admin" in pages else []),
    "Legal": [pages["privacy"], pages["terms"]],
}
current = st.navigation(nav, expanded=True)
ui.remember_current_page(pages, current)

with st.sidebar:
    quick = st.text_input("Search everything", key="global_q", placeholder="table, column, query, check…",
                          label_visibility="collapsed")
    if quick.strip() and quick != st.session_state.get("_global_q_last"):
        st.session_state["_global_q_last"] = quick
        ui.set_pending("gs_term", quick)
        ui.go("search")
    names = db.connection_names()
    if names:
        ui.active_connection()
        st.selectbox("Database", names, key="conn_name")
    else:
        st.caption("No database connected.")
    st.divider()
    st.caption(f"Signed in as **{u['full_name']}** · {u['role']}")
    legal_a, legal_b = st.columns(2)
    if legal_a.button("Privacy", key="side_privacy", type="tertiary"):
        ui.go("privacy")
    if legal_b.button("Terms", key="side_terms", type="tertiary"):
        ui.go("terms")
    if st.button("Sign out", width="stretch"):
        st.session_state.clear()
        st.rerun()

ui.insecure_warning()
ui.cookie_notice()
current.run()
