"""Evidence: collected results turned into a Jira-ready bug report. Always masked."""
import os

import streamlit as st

import db
import packs
import ui
from core import evidence

FORMATS = {"Jira": "jira", "Markdown": "markdown", "Plain text": "text"}


def render():
    items = ui.evidence_items()
    st.title("Bug evidence")
    st.caption("Collect results with “Add to evidence” anywhere in the app, then generate a bug report. "
               "Emails, phone numbers and secret fields are always masked here — even for admins.")

    if not items:
        st.info("Nothing collected yet. Use “Add to evidence” on a record, a query result, a timeline or an API comparison.")
    else:
        st.markdown(f"**Collected ({len(items)})**")
        for i, it in enumerate(items):
            with st.container(border=True):
                a, b = st.columns([6, 1], vertical_alignment="center")
                n = it.get("total_rows")
                rows = "" if n is None else f" · {n} row" + ("" if n == 1 else "s")
                a.markdown(f"**{it['title']}**  \n{it['kind']} · added {it['added_at']}{rows}")
                if b.button("Remove", key=f"ev_rm_{i}_{it['added_at']}"):
                    items.pop(i)
                    st.rerun()
        if st.button("Clear all evidence", type="tertiary"):
            items.clear()
            st.rerun()

    st.divider()
    st.subheader("Bug report")
    conn = ui.active_connection()
    env_default = f"{conn} ({db.database_name(conn)})" if conn else ""
    c1, c2 = st.columns(2)
    fields = {
        "title": c1.text_input("Bug title", key="ev_title", placeholder="[Module] Lead stays pending after successful payment"),
        "environment": c2.text_input("Environment", value=env_default, key="ev_env"),
        "identifier": c1.text_input("Record / identifier", key="ev_ident", placeholder="user_id = 12345"),
    }
    fields["summary"] = st.text_area("Investigation summary", key="ev_summary", height=80)
    fields["steps"] = st.text_area("Steps to reproduce", key="ev_steps", height=90, placeholder="1. …\n2. …\n3. …")
    d1, d2 = st.columns(2)
    fields["expected"] = d1.text_area("Expected result", key="ev_expected", height=80)
    fields["actual"] = d2.text_area("Actual result", key="ev_actual", height=80)

    fmt_label = st.segmented_control("Format", list(FORMATS), default="Jira", key="ev_fmt")
    fmt = FORMATS.get(fmt_label or "Jira", "jira")
    try:
        report = evidence.render(fields, items, fmt)
    except FileNotFoundError as e:
        st.error(f"Template missing: {e}. Restore config/templates/.")
        return
    st.caption("Use the copy icon at the top right of the box.")
    st.code(report, language=None, wrap_lines=True)
    ext = {"jira": "txt", "markdown": "md", "text": "txt"}[fmt]
    st.download_button(f"Download .{ext}", report.encode("utf-8"), file_name=f"bug_report.{ext}",
                       mime="text/plain", on_click="ignore")

    with st.expander("Template"):
        local_path = os.path.join(packs.LOCAL_DIR, "templates", f"bug_{fmt}.txt")
        st.caption(f"Placeholders: {', '.join('{' + p + '}' for p in evidence.PLACEHOLDERS)}. "
                   f"Your team’s version is saved to local/templates/bug_{fmt}.txt and replaces the default.")
        current = evidence.load_template(fmt)
        if ui.is_admin():
            edited = st.text_area("Template text", value=current, height=320, key=f"ev_tpl_{fmt}")
            x, y, _ = st.columns([1, 1.4, 4])
            if x.button("Save template", key=f"ev_tpl_save_{fmt}"):
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "w") as f:
                    f.write(edited)
                st.success("Saved.")
            if os.path.exists(local_path) and y.button("Restore default", key=f"ev_tpl_reset_{fmt}"):
                os.remove(local_path)
                st.rerun()
        else:
            st.code(current, language=None)
