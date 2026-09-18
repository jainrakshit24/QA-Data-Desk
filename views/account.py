# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""My account: profile and password."""
import streamlit as st

import auth
import ui


def render():
    st.title("My account")
    u = ui.user()
    st.markdown(f"**{u['full_name']}** · {u['username']} · {u['email']} · {u['role']}")
    with st.form("acc_pw", clear_on_submit=True):
        st.markdown("**Change password**")
        old = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password", help="At least 10 characters, with a letter and a number")
        again = st.text_input("New password again", type="password")
        if st.form_submit_button("Update password", type="primary"):
            if new != again:
                st.error("The new passwords do not match.")
            else:
                ok, msg = auth.change_password(u["id"], old, new)
                (st.success if ok else st.error)(msg)
