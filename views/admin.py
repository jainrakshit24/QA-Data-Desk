"""Admin: approve people, manage database connections, AI key and activity log."""
import pandas as pd
import streamlit as st

import ai
import auth
import db
import privacy
import store
import ui


def render():
    st.title("Admin")
    if not ui.is_admin():
        st.error("Only admins can open this page.")
        return
    tabs = ui.lazy_tabs(["People", "Databases", "AI", "Privacy", "Activity"], key="adm_tabs")
    for tab, draw in zip(tabs, (_people, _databases, _ai, _privacy, _activity)):
        if tab.open:
            with tab:
                draw()


def _people():
    users = auth.list_users()
    pending = [x for x in users if x["status"] == "pending"]
    if pending:
        st.info(f"{len(pending)} account{'s' if len(pending) != 1 else ''} waiting for approval.")
    st.dataframe(pd.DataFrame(users), width="stretch", hide_index=True)

    me = ui.user()
    labels = {x["id"]: f"{x['full_name']} ({x['username']}) · {x['role']} · {x['status']}" for x in users}
    uid = st.selectbox("Choose a person", list(labels), format_func=labels.get, key="adm_user")
    target = next(x for x in users if x["id"] == uid)
    admins_active = [x for x in users if x["role"] == "admin" and x["status"] == "active"]
    last_admin = target["role"] == "admin" and len(admins_active) <= 1

    cols = st.columns(5)
    if target["status"] != "active" and cols[0].button("Approve / enable", type="primary"):
        auth.set_status(uid, "active"); st.rerun()
    if target["status"] == "active" and cols[0].button("Disable", disabled=uid == me["id"] or last_admin,
                                                       help="You cannot disable yourself or the last admin"):
        auth.set_status(uid, "disabled"); st.rerun()
    if target["role"] == "user" and cols[1].button("Make admin"):
        auth.set_role(uid, "admin"); st.rerun()
    if target["role"] == "admin" and cols[1].button("Make regular user", disabled=last_admin):
        auth.set_role(uid, "user"); st.rerun()
    if cols[2].button("Reset password"):
        temp = auth.reset_password(uid)
        st.success(f"Temporary password for {target['username']}: `{temp}` — share it privately. "
                   "It is shown only once; they should change it under My account.")


def _databases():
    """Connections have their own page (Settings → Databases) so everyone can see and switch them."""
    conns = db.load_connections()
    st.caption("Every connection opens read-only. Passwords are stored in .db.json on this server "
               "(file readable only by its owner) or come from .env, and are never shown.")
    if conns:
        st.dataframe(pd.DataFrame([{k: c.get(k) for k in ("name", "host", "port", "database", "user")}
                                   for c in conns]), width="stretch", hide_index=True)
    else:
        st.info("No database is connected yet.")
    if st.button("Open the Databases page", type="primary", key="adm_db_open",
                 help="Add, test, edit or remove connections"):
        ui.go("databases")


def _ai():
    cfg = ai.settings()
    st.markdown(f"**Status:** {'On' if cfg['api_key'] else 'Off'} · model `{cfg['model']}`")
    st.caption("Gemini receives table names, column names, the question and SQL text. It never receives rows "
               "from the database. On Google’s free tier, prompts may be used to improve Google’s models.")
    with st.form("adm_ai"):
        key = st.text_input("Gemini API key", type="password",
                            placeholder="Leave empty to keep the current key" if cfg["api_key"] else "AIza…")
        model = st.text_input("Model", value=cfg["model"],
                              help="Any Gemini model name that supports generateContent, e.g. gemini-flash-lite-latest")
        if st.form_submit_button("Save", type="primary"):
            if not key.strip() and not cfg["api_key"]:
                st.error("Paste an API key first. Create one free at aistudio.google.com.")
            else:
                ai.save_settings(key, model)
                st.success("Saved.")
                st.rerun()
    a, b, _ = st.columns([1, 1, 4])
    if a.button("Test AI", disabled=not cfg["api_key"]):
        with st.spinner("Calling Gemini…"):
            try:
                out = ai._call('Reply with JSON {"ok": true}', "You are a connection test.")
                st.success(f"Gemini answered: {out}")
            except ai.AIError as e:
                st.error(str(e))
    if b.button("Remove key", disabled=not cfg["api_key"]):
        ai.clear_key()
        st.rerun()
    with st.expander("Notes sent to AI with every question (config/ai_context.md + local/ai_context.md)"):
        st.code(ai.domain_notes() or "(empty)", language="markdown")


def _activity():
    rows = auth.recent_log(limit=500)
    if not rows:
        st.caption("No queries run yet.")
        return
    df = pd.DataFrame(rows)[["ran_at", "username", "source", "rows", "seconds", "error", "sql"]]
    who = st.selectbox("Person", ["Everyone"] + sorted(df["username"].dropna().unique().tolist()), key="adm_log_who")
    if who != "Everyone":
        df = df[df["username"] == who]
    st.dataframe(df, width="stretch", hide_index=True, height=460)


def _privacy():
    st.caption("Built-in protection always applies. Add column names used in your databases: a name matches a whole "
               "column name or a part between underscores (e.g. “bank” also covers bank_account_no).")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Always hidden** (secret)")
        st.caption("Hidden for everyone, including admins; queries that name them are refused; masked in history, "
                   "reports and evidence. Built in: " + ", ".join(privacy.BASE_SECRET_PARTS))
        secret_text = st.text_area("Extra secret column names", ", ".join(privacy.EXTRA_SECRET), height=110,
                                   key="adm_priv_secret", placeholder="bank_account, ifsc, cvv")
    with c2:
        st.markdown("**Masked for non-admins** (personal)")
        st.caption("Values are partly masked for regular users, like phone numbers. Built in: "
                   + ", ".join(privacy.BASE_PERSONAL_PARTS) + ", and every email address")
        personal_text = st.text_area("Extra personal column names", ", ".join(privacy.EXTRA_PERSONAL), height=110,
                                     key="adm_priv_personal", placeholder="guardian_phone, alternate_number")
    secret, bad1 = privacy.parse_names(secret_text)
    personal, bad2 = privacy.parse_names(personal_text)
    if bad1 or bad2:
        st.warning("Ignored (use letters, numbers and underscores only): " + ", ".join(bad1 + bad2))
    if st.button("Save privacy settings", type="primary", key="adm_priv_save"):
        store.set_setting("privacy.secret_columns", secret)
        store.set_setting("privacy.personal_columns", personal)
        store.apply_privacy_settings()
        st.success(f"Saved — {len(secret)} extra secret and {len(personal)} extra personal names now apply everywhere.")
