# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Loading and running QA checks for the Checks and Dashboard pages (records every run's counts)."""
import streamlit as st

import db
import packs
import store
import ui
from core import checks as core_checks

ROW_LIMIT = 5000


def load(database=None):
    items = [core_checks.normalise(c) for c in packs.load_list("checks") if c.get("sql")]
    return core_checks.filter_checks(items, database=database) if database else items


def run(checks):
    """Run checks in parallel through the read-only layer and store their counts. Returns {id: result}."""
    if not checks:
        return {}
    conn = ui.active_connection()
    previous = store.latest_runs(conn)
    done = dict(ui.run_many([(c["id"], c["sql"], None) for c in checks], source="checks", row_limit=ROW_LIMIT))
    who = (ui.user() or {}).get("id")
    for c in checks:
        res = done.get(c["id"])
        status = core_checks.evaluate(c, res)
        rows = None if status in ("error", "outdated") else core_checks.result_rows(res)
        prev = (previous.get(c["id"]) or {}).get("latest")
        res["status"] = status
        res["previous_rows"] = prev["result_rows"] if prev else None
        res["alerts"] = core_checks.alerts(c, status, rows, res.get("seconds"), res["previous_rows"])
        store.record_check_run(c, conn, status, rows, round(res.get("seconds") or 0, 2), res.get("error"), who)
    return done


def database_name():
    return db.database_name(ui.active_connection())


def results_state():
    return st.session_state.setdefault("chk_results", {})
