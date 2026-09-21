# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Privacy policy and terms of use, readable before and after signing in."""
import streamlit as st

from core import legal


def _page(name):
    st.markdown(legal.document(name))
    if not legal.is_customised(name):
        st.caption("This is the text that ships with the software. An administrator can replace it with your "
                   "organisation's own wording by adding `local/legal/" + name + ".md`, and set QA_ORG_NAME and "
                   "QA_CONTACT_EMAIL in `.env` so it names the right people.")
    st.divider()
    a, b, _ = st.columns([1.2, 1.2, 4])
    a.download_button("Download", legal.document(name).encode("utf-8"), file_name=f"{name}.md",
                      mime="text/markdown", on_click="ignore", key=f"legal_dl_{name}")
    if b.button("The other document", key=f"legal_other_{name}"):
        import ui
        ui.go("terms" if name == "privacy" else "privacy")


def privacy():
    _page("privacy")


def terms():
    _page("terms")
