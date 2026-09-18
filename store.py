# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""App-side storage for Phase 2 features, in the same SQLite file as accounts.

Only metadata is stored — check result counts, rule and playbook definitions, schema structure.
Never database rows.
"""
import datetime as dt
import json

import auth


def _now():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat()


def init():
    with auth.conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS check_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_id TEXT NOT NULL,
            title TEXT,
            area TEXT,
            severity TEXT,
            connection TEXT,
            status TEXT NOT NULL,          -- ok | failed | info | error | outdated
            result_rows INTEGER,
            seconds REAL,
            error TEXT,
            user_id INTEGER,
            ran_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_check_runs ON check_runs (check_id, connection, ran_at);
        CREATE TABLE IF NOT EXISTS consistency_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            shared INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS playbooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            shared INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS schema_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            connection TEXT NOT NULL,
            database_name TEXT,
            tables INTEGER,
            columns INTEGER,
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS record_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            connection TEXT NOT NULL,
            table_name TEXT NOT NULL,
            key_column TEXT NOT NULL,
            key_digest TEXT NOT NULL,
            key_label TEXT,
            label TEXT,
            data_json TEXT NOT NULL,        -- masked values + fingerprints only
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_record_snapshots ON record_snapshots (connection, table_name, key_column, key_digest);
        """)


# ------------------------------------------------------------------ check runs
def record_check_run(check, connection, status, result_rows, seconds, error, user_id):
    with auth.conn() as c:
        c.execute("INSERT INTO check_runs (check_id, title, area, severity, connection, status, result_rows, seconds, "
                  "error, user_id, ran_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (check["id"], check.get("title"), check.get("area"), check.get("severity"), connection, status,
                   result_rows, seconds, error, user_id, _now()))


def check_history(connection, check_id=None, limit=500):
    with auth.conn() as c:
        if check_id:
            rows = c.execute("SELECT * FROM check_runs WHERE connection=? AND check_id=? ORDER BY id DESC LIMIT ?",
                             (connection, check_id, limit))
        else:
            rows = c.execute("SELECT * FROM check_runs WHERE connection=? ORDER BY id DESC LIMIT ?", (connection, limit))
        return [dict(r) for r in rows]


def latest_runs(connection):
    """For each check: its latest run and the one before it (for change detection)."""
    with auth.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM check_runs WHERE connection=? ORDER BY check_id, id DESC", (connection,))]
    out = {}
    for r in rows:
        slot = out.setdefault(r["check_id"], [])
        if len(slot) < 2:
            slot.append(r)
    return {k: {"latest": v[0], "previous": v[1] if len(v) > 1 else None} for k, v in out.items()}


# ------------------------------------------------------------------ shared definitions
def _save(table, user_id, name, spec, shared, item_id=None):
    now = _now()
    with auth.conn() as c:
        if item_id:
            c.execute(f"UPDATE {table} SET name=?, spec_json=?, shared=?, updated_at=? WHERE id=?",
                      (name.strip(), json.dumps(spec), int(shared), now, item_id))
            return item_id
        cur = c.execute(f"INSERT INTO {table} (user_id, name, spec_json, shared, created_at, updated_at) "
                        f"VALUES (?,?,?,?,?,?)", (user_id, name.strip(), json.dumps(spec), int(shared), now, now))
        return cur.lastrowid


def _list(table, user_id):
    with auth.conn() as c:
        rows = c.execute(f"SELECT t.*, u.username AS owner FROM {table} t JOIN users u ON u.id = t.user_id "
                         f"WHERE t.user_id = ? OR t.shared = 1 ORDER BY t.updated_at DESC", (user_id,)).fetchall()
    return [dict(r) | {"spec": json.loads(r["spec_json"])} for r in rows]


def _delete(table, item_id, user_id, is_admin):
    with auth.conn() as c:
        if is_admin:
            c.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
        else:
            c.execute(f"DELETE FROM {table} WHERE id = ? AND user_id = ?", (item_id, user_id))


def save_rule(user_id, name, spec, shared=True, rule_id=None):
    return _save("consistency_rules", user_id, name, spec, shared, rule_id)


def rules(user_id):
    return _list("consistency_rules", user_id)


def delete_rule(rule_id, user_id, is_admin=False):
    _delete("consistency_rules", rule_id, user_id, is_admin)


def save_playbook(user_id, name, spec, shared=False, playbook_id=None):
    return _save("playbooks", user_id, name, spec, shared, playbook_id)


def playbooks(user_id):
    return _list("playbooks", user_id)


def delete_playbook(playbook_id, user_id, is_admin=False):
    _delete("playbooks", playbook_id, user_id, is_admin)


# ------------------------------------------------------------------ schema snapshots
def save_snapshot(user_id, name, connection, database_name, schema_obj):
    data = {"tables": schema_obj.tables[["name", "rows_est"]].to_dict("records"),
            "columns": schema_obj.columns[["tbl", "col", "type", "nullable", "key", "default", "extra"]]
            .astype(str).to_dict("records"),
            "fks": schema_obj.fks.to_dict("records")}
    with auth.conn() as c:
        c.execute("INSERT INTO schema_snapshots (user_id, name, connection, database_name, tables, columns, data_json, "
                  "created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (user_id, name.strip(), connection, database_name, len(schema_obj.names), len(schema_obj.columns),
                   json.dumps(data), _now()))


def snapshots():
    with auth.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, name, connection, database_name, tables, columns, created_at FROM schema_snapshots "
            "ORDER BY id DESC")]


def snapshot_data(snapshot_id):
    with auth.conn() as c:
        r = c.execute("SELECT data_json FROM schema_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return json.loads(r["data_json"]) if r else None


def delete_snapshot(snapshot_id):
    with auth.conn() as c:
        c.execute("DELETE FROM schema_snapshots WHERE id = ?", (snapshot_id,))


# ------------------------------------------------------------------ record snapshots (masked)
def snapshot_key():
    """Local secret for record fingerprints; kept outside the app database."""
    import os
    import secrets
    path = os.path.join(os.path.dirname(os.path.abspath(auth.APP_DB)), ".snapshot_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(path, 0o600)
    with open(path) as f:
        return bytes.fromhex(f.read().strip())


def save_record_snapshot(user_id, connection, label, snap):
    with auth.conn() as c:
        cur = c.execute("INSERT INTO record_snapshots (user_id, connection, table_name, key_column, key_digest, key_label, "
                        "label, data_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (user_id, connection, snap["table"], snap["key_column"], snap["key_digest"], snap["key_label"],
                         (label or "").strip(), json.dumps(snap, default=str), _now()))
        return cur.lastrowid


def record_snapshots(connection, table, key_column, key_digest):
    with auth.conn() as c:
        rows = c.execute("SELECT s.*, u.username AS owner FROM record_snapshots s LEFT JOIN users u ON u.id = s.user_id "
                         "WHERE connection=? AND table_name=? AND key_column=? AND key_digest=? ORDER BY s.id DESC",
                         (connection, table, key_column, key_digest)).fetchall()
    return [dict(r) | {"data": json.loads(r["data_json"])} for r in rows]


def delete_record_snapshot(snapshot_id, user_id, is_admin=False):
    _delete("record_snapshots", snapshot_id, user_id, is_admin)


# ------------------------------------------------------------------ settings
def get_setting(key, default=None):
    with auth.conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_setting(key, value):
    with auth.conn() as c:
        c.execute("INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                  (key, json.dumps(value), _now()))


def apply_privacy_settings():
    """Load admin-configured secret / personal column names into the privacy module."""
    import privacy
    privacy.configure(get_setting("privacy.secret_columns", []), get_setting("privacy.personal_columns", []))
