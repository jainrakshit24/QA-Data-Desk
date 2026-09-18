# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Databases: connect your own database, test it, edit it, switch between connections.

Connections live in `.db.json` on this server (owner-readable only) or come from the environment (.env / QA_DB_*).
Every connection is opened read-only, so connecting one can never change its data.
"""
import pandas as pd
import streamlit as st

import db
import schema as schema_mod
import ui

FIELDS = ("name", "host", "port", "user", "password", "database")


def render():
    st.title("Databases")
    st.caption("Connect a database, switch between them, or update the details. Every connection is opened "
               "read-only — the app cannot change data in any database you add.")
    conns = db.load_connections()
    admin = ui.is_admin()

    if conns:
        rows = []
        for c in conns:
            rows.append({"name": c["name"], "host": c.get("host"), "port": int(c.get("port") or 3306),
                         "database": c.get("database"), "username": c.get("user"),
                         "from": "environment" if db.is_from_env(c["name"]) else ".db.json",
                         "active": "← in use" if c["name"] == ui.active_connection() else ""})
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption("Passwords are never shown. A connection marked “environment” comes from .env or QA_DB_* "
                   "and is changed there, not here.")
        _switch(conns)
    else:
        st.info("No database is connected yet. Add one below — you need the host, database name, username and "
                "password of a **read-only** database user.")

    if not admin:
        if conns:
            st.caption("Only admins can add or change connections. Ask an admin if you need another database.")
        return

    _add(conns)
    if conns:
        _edit(conns)


def _switch(conns):
    a, b, c = st.columns([3, 1.2, 1.6], vertical_alignment="bottom")
    names = [x["name"] for x in conns]
    current = ui.active_connection()
    pick = a.selectbox("Use this database", names, index=names.index(current) if current in names else 0,
                       key="dbp_use")
    if b.button("Switch", disabled=pick == current, key="dbp_switch"):
        st.session_state.conn_name = pick
        st.rerun()
    if c.button("Test connection", key="dbp_test"):
        _test(db.load_config(pick))
    if ui.is_admin() and st.button("Refresh table list for this database", key="dbp_refresh",
                                   help="Read tables and columns again instead of waiting for the automatic check"):
        with st.spinner("Reading the database structure…"):
            schema_mod.load(pick, force=True)
            if pick == current:
                ui.refresh_schema()
        st.success("Table list refreshed.")


def _test(cfg):
    with st.spinner(f"Connecting to {cfg['database']} at {cfg['host']}…"):
        try:
            info, secs = db.ping(cfg=cfg)
            st.success(f"Connected to {info['db']} (MySQL {info['version']}) in {secs:.1f}s. "
                       f"Server time {info['server_time']}.")
            return True
        except Exception as e:
            ui.show_error({"error": ui.error_help(e)["message"], "help": ui.error_help(e)})
            return False


def _form_values(prefix, initial=None):
    initial = initial or {}
    name = st.text_input("Label", value=initial.get("name", ""), key=f"{prefix}_name",
                         placeholder="Staging · shop", help="Shown in the sidebar and in the activity log")
    host = st.text_input("Host", value=initial.get("host", ""), key=f"{prefix}_host",
                         placeholder="db.staging.example.com")
    c1, c2 = st.columns(2)
    port = c1.number_input("Port", 1, 65535, int(initial.get("port", 3306)), key=f"{prefix}_port")
    database = c2.text_input("Database", value=initial.get("database", ""), key=f"{prefix}_db")
    c3, c4 = st.columns(2)
    user = c3.text_input("Username", value=initial.get("user", ""), key=f"{prefix}_user")
    password = c4.text_input("Password", type="password", key=f"{prefix}_pw",
                             placeholder="Leave empty to keep the saved password" if initial else "")
    return {"name": name, "host": host, "port": port, "user": user, "password": password, "database": database}


def _add(conns):
    # Any rerun (pressing Enter in a field, a failed test) would otherwise collapse a half-filled form.
    started = any(str(st.session_state.get(f"dbp_new_{k}", "")).strip() for k in ("name", "host", "db", "user", "pw")) \
        or bool(str(st.session_state.get("dbp_url", "")).strip())
    with st.expander("Add a database", expanded=st.session_state.get("dbp_add_open", not conns) or started):
        st.caption("Ask your DBA for a user with SELECT permission only. The app enforces read-only anyway, "
                   "but a read-only user is the safer setup.")
        with st.expander("Paste a connection URL instead",
                         expanded=bool(str(st.session_state.get("dbp_url", "")).strip())):
            url = st.text_input("mysql://user:password@host:3306/database", key="dbp_url")
            if st.button("Fill the form from this URL", key="dbp_url_go", disabled=not url.strip()):
                try:
                    parsed = db.parse_url(url)
                except ValueError as e:
                    st.error(str(e))
                else:
                    for field, value in parsed.items():
                        ui.set_pending(f"dbp_new_{'db' if field == 'database' else 'pw' if field == 'password' else field}",
                                       value)
                    ui.set_pending("dbp_new_name", f"{parsed['database']} @ {parsed['host']}")
                    st.rerun()
        for key in ("name", "host", "port", "db", "user", "pw"):
            ui.apply_pending(f"dbp_new_{key}")
        values = _form_values("dbp_new")
        a, b, _ = st.columns([1.4, 1.4, 4])
        test_only = a.button("Test connection", key="dbp_new_test")
        save = b.button("Test and save", type="primary", key="dbp_new_save")
        if not (test_only or save):
            return
        try:
            cfg = db.clean_config(values)
        except ValueError as e:
            st.error(str(e))
            return
        if save and cfg["name"] in {c["name"] for c in conns}:
            st.error(f"A connection called “{cfg['name']}” already exists. Choose another label.")
            return
        if not _test(cfg):
            st.caption("Nothing was saved. Fix the details above and try again.")
            return
        if save:
            db.save_connections([c for c in conns if not db.is_from_env(c["name"])] + [cfg])
            st.session_state.conn_name = cfg["name"]
            st.success(f"Saved and switched to “{cfg['name']}”.")
            st.rerun()


def _edit(conns):
    editable = [c for c in conns if not db.is_from_env(c["name"])]
    if not editable:
        st.caption("The only connection comes from the environment, so it is changed in .env, not here.")
        return
    with st.expander("Change or remove a database"):
        names = [c["name"] for c in editable]
        pick = st.selectbox("Connection", names, key="dbp_edit_pick")
        chosen = next(c for c in editable if c["name"] == pick)
        prefix = f"dbp_edit_{pick}"
        values = _form_values(prefix, chosen)
        a, b, c_, _ = st.columns([1.3, 1.3, 1.3, 3])
        if a.button("Test", key=f"{prefix}_test"):
            cfg = dict(chosen) | {k: v for k, v in values.items() if k != "password" or v}
            try:
                _test(db.clean_config(cfg))
            except ValueError as e:
                st.error(str(e))
        if b.button("Save changes", type="primary", key=f"{prefix}_save"):
            try:
                cfg = db.clean_config(dict(chosen) | {k: v for k, v in values.items() if k != "password" or v})
            except ValueError as e:
                st.error(str(e))
                return
            if cfg["name"] != pick and cfg["name"] in {x["name"] for x in conns}:
                st.error(f"A connection called “{cfg['name']}” already exists.")
                return
            if not _test(cfg):
                st.caption("Nothing was changed.")
                return
            db.save_connections([cfg if x["name"] == pick else x for x in editable])
            if st.session_state.get("conn_name") == pick:
                st.session_state.conn_name = cfg["name"]
            st.success("Saved.")
            st.rerun()
        with c_.popover("Remove"):
            st.write(f"Remove **{pick}**? Saved queries and check history stay, and the database itself is "
                     "never touched.")
            if st.button("Yes, remove it", key=f"{prefix}_rm"):
                db.save_connections([x for x in editable if x["name"] != pick])
                if st.session_state.get("conn_name") == pick:
                    st.session_state.pop("conn_name", None)
                st.rerun()
