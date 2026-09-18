"""Accounts, sign-in and the app's own small database (SQLite).

Passwords are stored as salted scrypt hashes. New sign-ups wait for an admin to
approve them; the very first account ever created becomes the admin.
"""
import datetime as dt
import hashlib
import hmac
import os
import re
import secrets
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DB = os.environ.get("QA_APP_DB", os.path.join(HERE, "app_data.sqlite3"))

SETUP_CODE_FILE = os.path.join(os.path.dirname(APP_DB), ".setup_code")

MAX_FAILED = 5
LOCK_MINUTES = 15
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")


def _now():
    return dt.datetime.utcnow().replace(microsecond=0)


def conn():
    c = sqlite3.connect(APP_DB, detect_types=sqlite3.PARSE_DECLTYPES)
    c.row_factory = sqlite3.Row
    return c


def _add_column(c, table, column, decl):
    cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL,
            pw_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',          -- user | admin
            status TEXT NOT NULL DEFAULT 'pending',     -- pending | active | disabled
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until TEXT,
            created_at TEXT NOT NULL,
            last_login TEXT
        );
        CREATE TABLE IF NOT EXISTS saved_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            notes TEXT,
            sql TEXT NOT NULL,
            shared INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rule_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            rules_json TEXT NOT NULL,
            shared INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS query_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            source TEXT,
            sql TEXT NOT NULL,
            rows INTEGER,
            seconds REAL,
            error TEXT,
            ran_at TEXT NOT NULL
        );
        """)
        _add_column(c, "query_log", "connection", "TEXT")
        _add_column(c, "saved_queries", "tags", "TEXT NOT NULL DEFAULT ''")
        _add_column(c, "saved_queries", "category", "TEXT NOT NULL DEFAULT ''")
        _add_column(c, "saved_queries", "updated_at", "TEXT")
        _add_column(c, "saved_queries", "last_executed", "TEXT")
        _add_column(c, "saved_queries", "execution_count", "INTEGER NOT NULL DEFAULT 0")


# ---------------------------------------------------------------- passwords
def hash_password(password):
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${key.hex()}"


def verify_password(password, stored):
    try:
        _, n, r, p, salt, key = stored.split("$")
        test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                              n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(key)))
        return hmac.compare_digest(test.hex(), key)
    except (ValueError, TypeError):
        return False


def password_problem(password):
    if len(password) < 10:
        return "Use at least 10 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Include at least one letter and one number."
    return None


# ---------------------------------------------------------------- first-run setup
def setup_code():
    """One-time code needed to create the first admin, so a public server cannot be claimed by a stranger.

    Uses QA_SETUP_CODE if set; otherwise a random code saved next to the app database.
    """
    if os.environ.get("QA_SETUP_CODE"):
        return os.environ["QA_SETUP_CODE"]
    if not os.path.exists(SETUP_CODE_FILE):
        with open(SETUP_CODE_FILE, "w") as f:
            f.write(secrets.token_hex(4).upper())
        os.chmod(SETUP_CODE_FILE, 0o600)
    with open(SETUP_CODE_FILE) as f:
        return f.read().strip()


def _clear_setup_code():
    if os.path.exists(SETUP_CODE_FILE):
        os.remove(SETUP_CODE_FILE)


# ---------------------------------------------------------------- accounts
def user_count():
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def sign_up(username, full_name, email, password, code=None):
    """Returns (ok, message). The first account becomes an active admin and needs the setup code."""
    username = (username or "").strip().lower()
    full_name = (full_name or "").strip()
    email = (email or "").strip()
    if not USERNAME_RE.match(username):
        return False, "Username must be 3–32 characters: lowercase letters, numbers, dot, dash or underscore."
    if not full_name:
        return False, "Enter your full name."
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return False, "Enter a valid email address."
    problem = password_problem(password)
    if problem:
        return False, problem
    first = user_count() == 0
    if first and not hmac.compare_digest((code or "").strip().upper(), setup_code().upper()):
        return False, "The setup code is wrong. It is printed in the terminal where the app was started."
    try:
        with conn() as c:
            c.execute(
                "INSERT INTO users (username, full_name, email, pw_hash, role, status, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (username, full_name, email, hash_password(password),
                 "admin" if first else "user", "active" if first else "pending", _now().isoformat()),
            )
    except sqlite3.IntegrityError:
        return False, "That username is taken. Choose another."
    if first:
        _clear_setup_code()
        return True, "Admin account created. You can sign in now."
    return True, "Account created. An admin needs to approve it before you can sign in."


def sign_in(username, password):
    """Returns (user_row or None, message)."""
    username = (username or "").strip().lower()
    with conn() as c:
        u = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not u:
            hash_password(password or "x")  # keep timing similar for unknown users
            return None, "Username or password is incorrect."
        if u["locked_until"] and dt.datetime.fromisoformat(u["locked_until"]) > _now():
            secs = (dt.datetime.fromisoformat(u["locked_until"]) - _now()).total_seconds()
            mins = max(1, -(-int(secs) // 60))
            return None, f"Too many failed attempts. Try again in {mins} minute(s)."
        if not verify_password(password or "", u["pw_hash"]):
            fails = u["failed_attempts"] + 1
            locked = (_now() + dt.timedelta(minutes=LOCK_MINUTES)).isoformat() if fails >= MAX_FAILED else None
            c.execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
                      (0 if locked else fails, locked, u["id"]))
            if locked:
                return None, f"Too many failed attempts. Account locked for {LOCK_MINUTES} minutes."
            return None, "Username or password is incorrect."
        if u["status"] == "pending":
            return None, "Your account is waiting for admin approval."
        if u["status"] == "disabled":
            return None, "This account has been disabled. Contact an admin."
        c.execute("UPDATE users SET failed_attempts=0, locked_until=NULL, last_login=? WHERE id=?",
                  (_now().isoformat(), u["id"]))
        return dict(u), "Signed in."


def change_password(user_id, old, new):
    with conn() as c:
        u = c.execute("SELECT pw_hash FROM users WHERE id=?", (user_id,)).fetchone()
        if not u or not verify_password(old, u["pw_hash"]):
            return False, "Current password is incorrect."
        problem = password_problem(new)
        if problem:
            return False, problem
        c.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(new), user_id))
    return True, "Password updated."


def list_users():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, username, full_name, email, role, status, created_at, last_login "
            "FROM users ORDER BY status='pending' DESC, created_at DESC")]


def set_status(user_id, status):
    with conn() as c:
        c.execute("UPDATE users SET status=?, failed_attempts=0, locked_until=NULL WHERE id=?",
                  (status, user_id))


def set_role(user_id, role):
    with conn() as c:
        c.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))


def reset_password(user_id):
    temp = secrets.token_urlsafe(9) + "7"
    with conn() as c:
        c.execute("UPDATE users SET pw_hash=?, failed_attempts=0, locked_until=NULL WHERE id=?",
                  (hash_password(temp), user_id))
    return temp


def get_user(user_id):
    with conn() as c:
        r = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(r) if r else None


# ---------------------------------------------------------------- saved queries & log
def _tags(tags):
    items = tags if isinstance(tags, (list, tuple)) else str(tags or "").split(",")
    return ", ".join(dict.fromkeys(t.strip().lower() for t in items if t.strip()))


def save_query(user_id, title, sql, notes="", shared=False, tags="", category=""):
    now = _now().isoformat()
    with conn() as c:
        cur = c.execute("INSERT INTO saved_queries (user_id, title, notes, sql, shared, created_at, updated_at, tags, category) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (user_id, title.strip(), (notes or "").strip(), sql.strip(), int(shared), now, now,
                         _tags(tags), (category or "").strip()))
        return cur.lastrowid


def update_query(query_id, user_id, is_admin=False, **fields):
    allowed = {"title", "notes", "sql", "shared", "tags", "category"}
    sets = {k: (_tags(v) if k == "tags" else int(v) if k == "shared" else str(v).strip())
            for k, v in fields.items() if k in allowed}
    if not sets:
        return
    sets["updated_at"] = _now().isoformat()
    cols = ", ".join(f"{k}=?" for k in sets)
    with conn() as c:
        if is_admin:
            c.execute(f"UPDATE saved_queries SET {cols} WHERE id=?", (*sets.values(), query_id))
        else:
            c.execute(f"UPDATE saved_queries SET {cols} WHERE id=? AND user_id=?", (*sets.values(), query_id, user_id))


def mark_query_run(query_id):
    with conn() as c:
        c.execute("UPDATE saved_queries SET execution_count = execution_count + 1, last_executed=? WHERE id=?",
                  (_now().isoformat(), query_id))


def saved_queries(user_id):
    with conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT q.*, u.username AS owner FROM saved_queries q JOIN users u ON u.id=q.user_id "
            "WHERE q.user_id=? OR q.shared=1 ORDER BY q.created_at DESC", (user_id,))]
    for r in rows:
        r["tag_list"] = [t for t in (r.get("tags") or "").split(", ") if t]
        r["updated_at"] = r.get("updated_at") or r["created_at"]
    return rows


def delete_query(query_id, user_id, is_admin=False):
    with conn() as c:
        if is_admin:
            c.execute("DELETE FROM saved_queries WHERE id=?", (query_id,))
        else:
            c.execute("DELETE FROM saved_queries WHERE id=? AND user_id=?", (query_id, user_id))


def log_query(user, source, sql, rows=None, seconds=None, error=None, connection=None):
    """Record that a query ran. Only its text (with sensitive values masked), counts and timing — never results."""
    import privacy
    with conn() as c:
        c.execute("INSERT INTO query_log (user_id, username, source, sql, rows, seconds, error, ran_at, connection) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  (user.get("id") if user else None, user.get("username") if user else None,
                   source, privacy.scrub_sql(sql), rows, seconds, privacy.scrub_sql(error) if error else None,
                   _now().isoformat(), connection))


def recent_log(user_id=None, limit=200):
    with conn() as c:
        if user_id:
            rows = c.execute("SELECT * FROM query_log WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))
        else:
            rows = c.execute("SELECT * FROM query_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


# ---------------------------------------------------------------- API response rule sets
def save_rule_set(user_id, name, rules, shared=False):
    import json
    with conn() as c:
        c.execute("INSERT INTO rule_sets (user_id, name, rules_json, shared, created_at) VALUES (?,?,?,?,?)",
                  (user_id, name.strip(), json.dumps(rules), int(shared), _now().isoformat()))


def rule_sets(user_id):
    import json
    with conn() as c:
        rows = c.execute("SELECT r.*, u.username AS owner FROM rule_sets r JOIN users u ON u.id = r.user_id "
                         "WHERE r.user_id = ? OR r.shared = 1 ORDER BY r.created_at DESC", (user_id,)).fetchall()
    return [dict(r) | {"rules": json.loads(r["rules_json"])} for r in rows]


def delete_rule_set(rule_set_id, user_id, is_admin=False):
    with conn() as c:
        if is_admin:
            c.execute("DELETE FROM rule_sets WHERE id = ?", (rule_set_id,))
        else:
            c.execute("DELETE FROM rule_sets WHERE id = ? AND user_id = ?", (rule_set_id, user_id))
