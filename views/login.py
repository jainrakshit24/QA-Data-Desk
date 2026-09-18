"""Sign in and create account."""
import streamlit as st

import auth

APP_NAME = "QA Data Desk"


def render():
    _, mid, _ = st.columns([1, 1.4, 1])
    with mid:
        st.title(APP_NAME)
        st.caption("Find, filter and chart database records without writing SQL. Read-only.")
        if st.session_state.get("signup_message"):
            st.success(st.session_state.pop("signup_message"))
        first_run = auth.user_count() == 0
        if first_run:
            st.info("No accounts exist yet. The account you create now becomes the admin. "
                    "You need the setup code printed in the terminal where the app was started.")
            _sign_up(first_run=True)
            return
        tab_in, tab_up = st.tabs(["Sign in", "Create account"])
        with tab_in:
            with st.form("login"):
                username = st.text_input("Username", autocomplete="username")
                password = st.text_input("Password", type="password", autocomplete="current-password")
                if st.form_submit_button("Sign in", type="primary", width="stretch"):
                    user, msg = auth.sign_in(username, password)
                    if user:
                        st.session_state.user = {k: user[k] for k in
                                                 ("id", "username", "full_name", "email", "role", "status")}
                        st.rerun()
                    st.error(msg)
        with tab_up:
            _sign_up(first_run=False)


def _sign_up(first_run):
    with st.form("signup", clear_on_submit=False):
        code = st.text_input("Setup code", help="Printed in the terminal where the app was started") if first_run else None
        full_name = st.text_input("Full name")
        email = st.text_input("Work email")
        username = st.text_input("Username", help="3–32 characters: lowercase letters, numbers, . _ -")
        password = st.text_input("Password", type="password", help="At least 10 characters, with a letter and a number")
        again = st.text_input("Password again", type="password")
        label = "Create admin account" if first_run else "Create account"
        if st.form_submit_button(label, type="primary", width="stretch"):
            if password != again:
                st.error("The passwords do not match.")
                return
            ok, msg = auth.sign_up(username, full_name, email, password, code)
            if not ok:
                st.error(msg)
            elif first_run:
                st.session_state.signup_message = msg   # page switches to sign-in and shows this
                st.rerun()
            else:
                st.success(msg)
