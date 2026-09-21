# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Privacy policy and terms of use, readable before and after signing in."""
import streamlit as st

from core import legal


def _page(name):
    import ui
    fallback = "login" if not st.session_state.get("user") else "investigate"
    top, _ = st.columns([1, 4])
    with top:
        ui.back_button(fallback=fallback, key=f"legal_back_{name}")
    left, mid, right = st.columns([1, 5, 1])          # a reading column, not the full width
    with mid:
        st.markdown(legal.document(name))
        if not legal.is_customised(name):
            st.caption("This is the text that ships with the software. An administrator can replace it with your "
                       "organisation's own wording by adding `local/legal/" + name + ".md`, and set QA_ORG_NAME "
                       "and QA_CONTACT_EMAIL in `.env` so it names the right people.")
        st.divider()
        a, b, c, _ = st.columns([1.1, 1.3, 1.8, 2])
        with a:
            ui.back_button(fallback=fallback, key=f"legal_back_bottom_{name}")
        b.download_button("Download", legal.document(name).encode("utf-8"), file_name=f"{name}.md",
                          mime="text/markdown", on_click="ignore", key=f"legal_dl_{name}")
        other = "terms" if name == "privacy" else "privacy"
        if c.button(f"Read the {legal.DOCUMENTS[other].lower()} →", key=f"legal_other_{name}"):
            ui.go(other)


def privacy():
    _page("privacy")


def terms():
    _page("terms")
