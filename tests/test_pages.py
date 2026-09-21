# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""End-to-end page tests with Streamlit's AppTest (no browser needed).

The sign-in flow runs anywhere. Pages that read a database run only when one is configured.
"""
import os
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import auth  # noqa: E402
import db  # noqa: E402

HAS_DB = os.path.exists(os.path.join(ROOT, ".db.json")) or bool(os.environ.get("QA_DB_HOST"))


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "APP_DB", str(tmp_path / "app.sqlite3"))
    monkeypatch.setattr(auth, "SETUP_CODE_FILE", str(tmp_path / ".setup_code"))
    monkeypatch.setenv("QA_SETUP_CODE", "E2ECODE")
    auth.init()
    import store
    store.init()
    return tmp_path


def page_script(tmp_path, view):
    """A tiny script that renders one page, so each page can be tested on its own."""
    path = tmp_path / f"page_{view}.py"
    path.write_text(
        "import sys\n"
        f"sys.path.insert(0, {ROOT!r})\n"
        "import auth\n"
        f"auth.APP_DB = {str(tmp_path / 'app.sqlite3')!r}\n"
        f"auth.SETUP_CODE_FILE = {str(tmp_path / '.setup_code')!r}\n"
        "auth.init()\n"
        "import store\n"
        "store.init()\n"
        f"from views import {view}\n"
        f"{view}.render()\n"
    )
    return str(path)


def signed_in(username="owner", role_admin=True):
    ok, msg = auth.sign_up(username, "Owner Person", "owner@example.com", "Password1234", code="E2ECODE")
    assert ok, msg
    user, _ = auth.sign_in(username, "Password1234")
    return {k: user[k] for k in ("id", "username", "full_name", "email", "role", "status")}


def fail_messages(at):
    return [str(e.value) for e in at.exception]


# ------------------------------------------------------------------ sign-in, no database needed
def test_first_visit_asks_for_setup_code_and_creates_admin(app_db):
    at = AppTest.from_file(page_script(app_db, "login"), default_timeout=30).run()
    assert at.text_input[0].label == "Setup code"

    at.text_input[0].set_value("WRONG")
    for widget, value in zip(at.text_input[1:], ["Owner Person", "owner@example.com", "owner",
                                                 "Password1234", "Password1234"]):
        widget.set_value(value)
    at.button[0].click().run()
    assert "setup code is wrong" in at.error[0].value
    assert auth.user_count() == 0

    at.text_input[0].set_value("E2ECODE")
    at.button[0].click().run()
    assert not fail_messages(at)
    assert "Admin account created" in at.success[0].value
    assert auth.list_users()[0]["role"] == "admin"
    assert at.tabs[0].label == "Sign in"          # page moved on to sign-in


def test_sign_in_rejects_wrong_password_and_accepts_right_one(app_db):
    signed_in()
    at = AppTest.from_file(page_script(app_db, "login"), default_timeout=30).run()
    at.text_input[0].set_value("owner")
    at.text_input[1].set_value("not-the-password1")
    at.button[0].click().run()
    assert at.error[0].value == "Username or password is incorrect."

    at.text_input[1].set_value("Password1234")
    at.button[0].click().run()
    assert at.session_state["user"]["username"] == "owner"


def test_non_admin_cannot_open_admin_page(app_db):
    signed_in()
    auth.sign_up("guest", "Guest", "g@example.com", "Password1234")
    guest = [u for u in auth.list_users() if u["username"] == "guest"][0]
    auth.set_status(guest["id"], "active")
    user, _ = auth.sign_in("guest", "Password1234")
    at = AppTest.from_file(page_script(app_db, "admin"), default_timeout=30)
    at.session_state["user"] = {k: user[k] for k in ("id", "username", "full_name", "email", "role", "status")}
    at.run()
    assert [e.value for e in at.error] == ["Only admins can open this page."]
    assert not at.tabs


# ------------------------------------------------------------------ pages that read the database
@pytest.mark.skipif(not HAS_DB, reason="no database configured")
@pytest.mark.parametrize("view", ["investigate", "record", "validate", "evidence_page", "consistency", "playbooks",
                                  "dashboard", "schema_compare", "explorer", "lookup", "ask_ai", "checks", "charts",
                                  "sql_editor", "admin", "account", "search", "test_ideas", "health", "env_compare", "databases"])
def test_every_page_renders_without_errors(app_db, view):
    at = AppTest.from_file(page_script(app_db, view), default_timeout=180)
    at.session_state["user"] = signed_in()
    at.run()
    assert not fail_messages(at)


@pytest.mark.skipif(not HAS_DB, reason="no database configured")
def test_find_data_search_opens_best_table_and_filters_rows(app_db):
    df, _, _ = db.run("SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema = DATABASE() "
                      "AND TABLE_TYPE = 'BASE TABLE' AND TABLE_ROWS BETWEEN 1 AND 100000 ORDER BY TABLE_NAME LIMIT 1")
    table = df.iloc[0, 0]
    at = AppTest.from_file(page_script(app_db, "explorer"), default_timeout=180)
    at.session_state["user"] = signed_in()
    at.run()
    at.text_input(key="ex_term").set_value(table).run()
    assert at.subheader[0].value == table

    at.button(key=f"ex_run_{table}").click().run()
    result = at.session_state["ex_state"][table]["result"]
    assert result["error"] is None
    assert result["sql"].startswith("SELECT ") and f"FROM `{table}`" in result["sql"]
    assert not fail_messages(at)


@pytest.mark.skipif(not HAS_DB, reason="no database configured")
def test_sql_editor_blocks_writes_and_secret_columns(app_db):
    at = AppTest.from_file(page_script(app_db, "sql_editor"), default_timeout=120)
    at.session_state["user"] = signed_in()
    for query, message in [
        ("UPDATE some_table SET a = 1", "Only read queries can run here"),
        ("SELECT password FROM some_table", "secret fields are never shown"),
    ]:
        at.session_state["sql_text"] = query
        at.run()
        at.button[0].click().run()
        assert message in at.session_state["sq_result"]["error"]



# ------------------------------------------------------------------ Phase 1 flows
def test_evidence_page_builds_a_masked_report_without_a_database(app_db):
    import pandas as pd
    from core import evidence
    at = AppTest.from_file(page_script(app_db, "evidence_page"), default_timeout=60)
    at.session_state["user"] = signed_in()
    at.session_state["evidence"] = [evidence.make_item(
        "compare", "API vs DB", df=pd.DataFrame([{"field": "email", "api": "leak@example.com", "result": "Mismatch"}]),
        sql="SELECT email FROM users WHERE id = 1")]
    at.run()
    at.text_input(key="ev_title").set_value("Lead stays pending").run()
    assert not fail_messages(at)
    report = at.code[0].value
    assert "Lead stays pending" in report and "SELECT email FROM users" in report
    assert "leak@example.com" not in report


@pytest.mark.skipif(not HAS_DB, reason="no database configured")
def test_global_search_finds_a_real_identifier_and_opens_it(app_db):
    df, _, _ = db.run("SELECT c.TABLE_NAME, c.COLUMN_NAME FROM information_schema.columns c "
                      "JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
                      "WHERE c.table_schema = DATABASE() AND c.COLUMN_KEY = 'UNI' AND c.DATA_TYPE IN ('int','bigint') "
                      "AND c.COLUMN_NAME <> 'id' AND t.TABLE_ROWS BETWEEN 1 AND 1000000 ORDER BY t.TABLE_ROWS DESC LIMIT 10")
    val = None
    for table, column in df.itertuples(index=False):
        val, _, _ = db.run(f"SELECT `{column}` FROM `{table}` WHERE `{column}` IS NOT NULL LIMIT 1")
        if not val.empty:
            break
    if val is None or val.empty:
        pytest.skip("no data to search for")
    value = str(val.iloc[0, 0])
    at = AppTest.from_file(page_script(app_db, "investigate"), default_timeout=300)
    at.session_state["user"] = signed_in()
    at.run()
    at.text_input(key="inv_value").set_value(value).run()
    at.text_input(key="inv_tbl").set_value(table).run()
    at.button(key="inv_go").click().run()
    assert not fail_messages(at)
    hits = at.session_state["inv_result"]["hits"]
    assert ((hits["tbl"] == table) & (hits["col"] == column)).any()


@pytest.mark.skipif(not HAS_DB, reason="no database configured")
def test_api_validator_compares_a_real_row(app_db):
    df, _, _ = db.run("SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema = DATABASE() AND TABLE_TYPE='BASE TABLE' "
                      "AND TABLE_ROWS BETWEEN 10 AND 5000 AND TABLE_NAME IN (SELECT TABLE_NAME FROM information_schema.columns "
                      "WHERE table_schema = DATABASE() AND COLUMN_NAME = 'id' AND COLUMN_KEY = 'PRI') ORDER BY TABLE_NAME LIMIT 1")
    table = df.iloc[0, 0]
    row, _, _ = db.run(f"SELECT id FROM `{table}` ORDER BY id LIMIT 1")
    rid = int(row.iloc[0, 0])
    import json
    at = AppTest.from_file(page_script(app_db, "validate"), default_timeout=180)
    at.session_state["user"] = signed_in()
    at.run()
    at.text_area(key="val_json").set_value(json.dumps({"id": rid, "made_up_field": "x"})).run()
    at.selectbox(key="val_table").set_value(table).run()
    at.radio(key="val_how").set_value("a value I type").run()
    at.text_input(key="val_mv").set_value(str(rid)).run()
    at.button(key="val_load").click().run()
    assert not fail_messages(at)
    assert at.session_state["val_row"]["res"]["error"] is None
    assert any("Matched" == m.label for m in at.metric)



# ------------------------------------------------------------------ Phase 2 flows
@pytest.mark.skipif(not HAS_DB, reason="no database configured")
def test_dashboard_runs_checks_and_records_counts(app_db):
    import store
    at = AppTest.from_file(page_script(app_db, "dashboard"), default_timeout=300)
    at.session_state["user"] = signed_in()
    at.run()
    run_all = next(b for b in at.button if b.label.startswith("Run all"))
    run_all.click().run()
    assert not fail_messages(at)
    conn = at.session_state["conn_name"]
    assert store.latest_runs(conn), "every run should be recorded"
    assert any(m.label == "Checks run today" and int(str(m.value)) > 0 for m in at.metric)


@pytest.mark.skipif(not HAS_DB, reason="no database configured")
def test_query_analyzer_explains_a_full_scan_without_running_it(app_db):
    df, _, _ = db.run("SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema = DATABASE() "
                      "AND TABLE_TYPE = 'BASE TABLE' AND TABLE_ROWS > 50000 ORDER BY TABLE_ROWS LIMIT 1")
    table = df.iloc[0, 0]
    at = AppTest.from_file(page_script(app_db, "sql_editor"), default_timeout=120)
    at.session_state["user"] = signed_in()
    at.session_state["sql_text"] = f"SELECT * FROM `{table}`"
    at.run()
    next(b for b in at.button if b.label == "Analyze").click().run()
    assert not fail_messages(at)
    plan = at.session_state["sq_plan"]
    assert plan["error"] is None and any(t["access"] == "ALL" for t in plan["plan"]["tables"])
    assert "sq_result" not in at.session_state or at.session_state["sq_result"] is None


@pytest.mark.skipif(not HAS_DB, reason="no database configured")
def test_schema_snapshot_then_compare_with_live_shows_no_differences(app_db):
    at = AppTest.from_file(page_script(app_db, "schema_compare"), default_timeout=300)
    at.session_state["user"] = signed_in()
    at.run()
    at.button(key="sch_snap_save").click().run()
    at.button(key="sch_go").click().run()
    assert not fail_messages(at)
    diff = at.session_state["sch_result"]["diff"]
    assert diff.empty, diff.head()


@pytest.mark.skipif(not HAS_DB, reason="no database configured")
def test_playbook_template_runs_every_step_on_a_real_table(app_db):
    df, _, _ = db.run("SELECT c.TABLE_NAME, c.COLUMN_NAME FROM information_schema.columns c JOIN information_schema.tables t "
                      "ON t.table_schema = c.table_schema AND t.table_name = c.table_name WHERE c.table_schema = DATABASE() "
                      "AND c.COLUMN_KEY = 'PRI' AND c.COLUMN_NAME = 'id' AND t.TABLE_ROWS BETWEEN 100 AND 100000 LIMIT 1")
    table = df.iloc[0, 0]
    at = AppTest.from_file(page_script(app_db, "playbooks"), default_timeout=300)
    at.session_state["user"] = signed_in()
    at.session_state["pb_pick"] = "template:tpl_duplicates"
    at.session_state["_keep_pb_pick"] = "template:tpl_duplicates"
    at.run()
    at.selectbox(key="pbr_template:tpl_duplicates_p_table").set_value(table).run()
    at.selectbox(key="pbr_template:tpl_duplicates_p_column").set_value("id").run()
    next(b for b in at.button if b.label.startswith("Run all")).click().run()
    assert not fail_messages(at)
    results = at.session_state["pbr_template:tpl_duplicates_results"]
    assert len(results) == 2 and all(r["error"] is None for r in results.values())
    assert int(results[0]["df"].iloc[0]["duplicated_values"]) == 0     # a primary key has no duplicates


@pytest.mark.skipif(not HAS_DB, reason="no database configured")
def test_search_everything_lists_tables_and_opens_one(app_db):
    at = AppTest.from_file(page_script(app_db, "search"), default_timeout=180)
    at.session_state["user"] = signed_in()
    at.run()
    at.text_input(key="gs_term").set_value("user").run()
    assert not fail_messages(at)
    assert any("Table" in h.value for h in at.subheader)
    at.button(key="gs_open_0").click().run()
    assert at.session_state["ex_table"]


@pytest.mark.skipif(not HAS_DB, reason="no database configured")
def test_databases_page_lists_connections_and_hides_passwords(app_db):
    at = AppTest.from_file(page_script(app_db, "databases"), default_timeout=60)
    at.session_state["user"] = signed_in()
    at.run()
    assert not fail_messages(at)
    shown = at.dataframe[0].value
    assert "name" in shown.columns and "password" not in shown.columns
    assert db.connection_names()[0] in list(shown["name"])
    assert any("read-only" in c.value for c in at.caption)


def test_databases_page_without_a_connection_offers_to_add_one(app_db, monkeypatch):
    monkeypatch.setattr(db, "load_connections", lambda: [])
    at = AppTest.from_file(page_script(app_db, "databases"), default_timeout=30)
    at.session_state["user"] = signed_in()
    at.run()
    assert not fail_messages(at)
    assert any("No database is connected yet" in i.value for i in at.info)
    assert any(b.label == "Test and save" for b in at.button)


# ------------------------------------------------------------------ secrets must never reach the browser
def test_no_secret_reaches_the_rendered_page(app_db, monkeypatch):
    """Passwords, API keys and the setup code are server-side only — nothing renders them."""
    import ai
    monkeypatch.setattr(db, "load_connections", lambda: [
        {"name": "Staging", "host": "db.example.com", "port": 3306, "user": "qa",
         "password": "super-secret-db-password", "database": "shop"}])
    monkeypatch.setattr(ai, "settings", lambda: {"api_key": "AIzaSy-super-secret-key", "model": "gemini-flash-lite-latest"})
    rendered = []
    user = signed_in()
    for view in ("databases", "admin", "account", "login"):
        at = AppTest.from_file(page_script(app_db, view), default_timeout=30)
        if view != "login":
            at.session_state["user"] = user
        at.run()
        assert not fail_messages(at)
        rendered.append(repr(at.get("markdown")) + repr(at.get("caption")) + repr(at.get("dataframe")) +
                        repr(at.get("text_input")) + repr(at.get("code")) + repr(at.get("json")))
    page = "\n".join(rendered)
    assert "super-secret-db-password" not in page
    assert "AIzaSy-super-secret-key" not in page
    assert auth.setup_code() not in page
