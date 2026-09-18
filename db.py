"""Read-only access to MySQL databases.

Every connection is opened as a READ ONLY session, and every query is checked
before it runs, so nothing typed into the app can change data.
"""
import datetime as dt
import decimal
import json
import os
import re
import threading
import time

import pandas as pd
import pymysql
from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, ".db.json")

MAX_ROWS = 20000          # rows pulled into the browser per query
QUERY_TIMEOUT_MS = 60000  # MySQL stops any single SELECT after this
POOL_SIZE = 4             # idle connections kept open per database
POOL_IDLE_SECONDS = 300   # close idle connections after this long
RECONNECT_CODES = {0, 2006, 2013, 2014, 2055}  # connection dropped — safe to retry a read once
UNREACHABLE_CODES = {2002, 2003, 2005}          # cannot reach the server at all
UNREACHABLE_BACKOFF = 20                        # seconds to fail fast after the server was unreachable
_unreachable_until = {}

READ_VERBS = ("select", "show", "describe", "desc", "explain", "with", "table")
# A read verb can still hide a write (MySQL 8 allows WITH … UPDATE), so these
# words are refused anywhere outside strings. The READ ONLY session is the backstop.
WRITE_WORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|rename|grant|revoke)\b",
    re.I,
)


class ReadOnlyError(ValueError):
    """Raised when a query is not a plain read."""


ENV_KEYS = {"host": "QA_DB_HOST", "port": "QA_DB_PORT", "user": "QA_DB_USER",
            "password": "QA_DB_PASSWORD", "database": "QA_DB_NAME"}


def load_connections():
    """All configured databases as a list of dicts with a unique 'name'.

    Sources, merged: QA_DB_* environment variables (one connection, for deployment)
    and .db.json (any number, managed from the Admin page).
    """
    conns = []
    if all(os.environ.get(v) for v in ENV_KEYS.values()):
        c = {k: os.environ[v] for k, v in ENV_KEYS.items()}
        c["port"] = int(str(c["port"]).strip() or 3306)      # env values are strings; keep the type consistent
        c["name"] = os.environ.get("QA_DB_LABEL", f"{c['database']} (env)")
        conns.append(c)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            raw = json.load(f)
        items = raw.get("connections", []) if isinstance(raw, dict) and "connections" in raw else [raw]
        for c in items:
            c = dict(c)
            c["port"] = int(str(c.get("port") or 3306).strip())
            c.setdefault("name", f"{c.get('database')} @ {c.get('host')}")
            if c["name"] not in {x["name"] for x in conns}:
                conns.append(c)
    return conns


def connection_names():
    return [c["name"] for c in load_connections()]


def is_from_env(name):
    """Connections that come from the environment (.env / QA_DB_*) cannot be edited or removed in the app."""
    if not all(os.environ.get(v) for v in ENV_KEYS.values()):
        return False
    return name == os.environ.get("QA_DB_LABEL", f"{os.environ['QA_DB_NAME']} (env)")


def parse_url(url):
    """mysql://user:pass@host:3306/dbname → connection fields. Also accepts host:port/db without a scheme."""
    text = (url or "").strip()
    if not text:
        raise ValueError("Paste a connection URL first.")
    m = re.fullmatch(r"(?:(?:mysql|mariadb)(?:\+\w+)?://)?"
                     r"(?:(?P<user>[^:/@\s]+)(?::(?P<password>[^@\s]*))?@)?"
                     r"(?P<host>[^:/?\s]+)(?::(?P<port>\d+))?"
                     r"(?:/(?P<database>[^?\s/]+))?/?(?:\?.*)?", text)
    if not m or not m.group("host"):
        raise ValueError("That does not look like mysql://user:password@host:3306/database.")
    out = {"host": m.group("host"), "port": int(m.group("port") or 3306),
           "user": unquote(m.group("user") or ""), "password": unquote(m.group("password") or ""),
           "database": unquote(m.group("database") or "")}
    if not out["user"] or not out["database"]:
        raise ValueError("The URL needs a username and a database name: mysql://user:password@host:3306/database.")
    return out


def clean_config(cfg):
    """Validate and tidy what a form collected. Raises ValueError with a message meant for the screen."""
    out = {"name": str(cfg.get("name") or "").strip(), "host": str(cfg.get("host") or "").strip(),
           "user": str(cfg.get("user") or "").strip(), "password": str(cfg.get("password") or ""),
           "database": str(cfg.get("database") or "").strip()}
    try:
        out["port"] = int(cfg.get("port") or 3306)
    except (TypeError, ValueError):
        raise ValueError("Port must be a number, usually 3306.")
    missing = [label for label, key in (("host", "host"), ("database", "database"), ("username", "user"))
               if not out[key]]
    if missing:
        raise ValueError("Fill in " + ", ".join(missing) + ".")
    if not 1 <= out["port"] <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    out["name"] = out["name"] or f"{out['database']} @ {out['host']}"
    return out


def load_config(name=None):
    conns = load_connections()
    if not conns:
        raise RuntimeError(
            "No database connection configured. Add one on the Admin page, copy "
            ".db.json.example to .db.json, or set QA_DB_HOST, QA_DB_PORT, QA_DB_USER, "
            "QA_DB_PASSWORD and QA_DB_NAME."
        )
    if name is None:
        return conns[0]
    for c in conns:
        if c["name"] == name:
            return c
    raise RuntimeError(f"No database connection named “{name}”.")


def save_connections(conns):
    """Write the file-based connections (env connections are not saved)."""
    env_names = set()
    if all(os.environ.get(v) for v in ENV_KEYS.values()):
        env_names.add(os.environ.get("QA_DB_LABEL", f"{os.environ['QA_DB_NAME']} (env)"))
    keep = [{k: c[k] for k in ("name", "host", "port", "user", "password", "database")}
            for c in conns if c["name"] not in env_names]
    with open(CONFIG_PATH, "w") as f:
        json.dump({"connections": keep}, f, indent=2)
    os.chmod(CONFIG_PATH, 0o600)


def database_name(name=None):
    return load_config(name)["database"]


def _strip_comments_and_strings(sql):
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"'(?:[^'\\]|\\.)*'", "''", sql)
    sql = re.sub(r'"(?:[^"\\]|\\.)*"', '""', sql)
    sql = re.sub(r"`[^`]*`", "``", sql)
    sql = re.sub(r"(--\s|#)[^\n]*", " ", sql)
    return sql.strip()


EXECUTABLE_COMMENT = re.compile(r"/\*!")            # MySQL runs the text inside /*! ... */
LOCKING_READ = re.compile(r"\bfor\s+(update|share)\b|\block\s+in\s+share\s+mode\b", re.I)
BLOCKED_FUNCTIONS = re.compile(
    r"\b(load_file|get_lock|release_lock|release_all_locks|is_used_lock|is_free_lock)\s*\(", re.I)


def check_read_only(sql):
    """Return the query ready to run, or raise ReadOnlyError explaining why not."""
    if EXECUTABLE_COMMENT.search(sql or ""):
        raise ReadOnlyError("Executable comments (/*! … */) are not allowed — MySQL would run the text inside them.")
    cleaned = _strip_comments_and_strings(sql).rstrip(";").strip()
    if not cleaned:
        raise ReadOnlyError("The query is empty.")
    if ";" in cleaned:
        raise ReadOnlyError("Run one statement at a time — remove the extra semicolon.")
    first = cleaned.lstrip("( \t\n").split(None, 1)[0].lower() if cleaned.lstrip("( \t\n") else ""
    first = re.split(r"[^a-z]", first)[0]
    if first not in READ_VERBS:
        raise ReadOnlyError(
            f"Only read queries can run here (SELECT, SHOW, DESCRIBE, EXPLAIN, WITH). "
            f"This one starts with {first.upper() or 'something else'}."
        )
    hit = WRITE_WORDS.search(cleaned)
    if hit:
        raise ReadOnlyError(
            f"“{hit.group(0).upper()}” is not allowed — this dashboard only reads data."
        )
    if re.search(r"\binto\s+(outfile|dumpfile|@)", cleaned, re.I):
        raise ReadOnlyError("SELECT … INTO is not allowed.")
    if LOCKING_READ.search(cleaned):
        raise ReadOnlyError("Locking reads (FOR UPDATE / FOR SHARE / LOCK IN SHARE MODE) are not allowed.")
    fn = BLOCKED_FUNCTIONS.search(cleaned)
    if fn:
        raise ReadOnlyError(f"{fn.group(1).upper()}() is not allowed.")
    return sql.strip().rstrip(";")


_LIMIT_AT_END = re.compile(
    r"\blimit\s+\d+(\s*,\s*\d+|\s+offset\s+\d+)?\s*$", re.I)


def apply_row_limit(sql, limit):
    """Add LIMIT to a SELECT/WITH query that has none at the end.

    Returns (sql, added). Without this, `SELECT * FROM big_table` makes MySQL send every row
    (millions) before anything can be shown.
    """
    cleaned = _strip_comments_and_strings(sql).rstrip(";").strip()
    first = cleaned.split(None, 1)[0].lower() if cleaned else ""
    if first not in ("select", "with") or _LIMIT_AT_END.search(cleaned):
        return sql.strip().rstrip(";"), False
    return f"{sql.strip().rstrip(';')}\nLIMIT {int(limit)}", True


def connect(name=None, cfg=None):
    """A new connection whose session can only read."""
    c = cfg or load_config(name)
    where = (c["host"], int(c["port"]))
    wait = _unreachable_until.get(where, 0) - time.time()
    if wait > 0:
        # don't make every query in a page wait 15 seconds for the same unreachable server
        raise pymysql.err.OperationalError(2003, f"Database server {c['host']} was unreachable a moment ago "
                                                 f"(retrying automatically in {int(wait) + 1}s)")
    try:
        conn = pymysql.connect(
            host=c["host"], port=int(c["port"]), user=c["user"], password=c["password"],
            database=c["database"], charset="utf8mb4", connect_timeout=15, read_timeout=180,
            autocommit=True,  # every query sees fresh data, never a stale snapshot from an earlier one
        )
    except pymysql.err.OperationalError as e:
        if e.args and e.args[0] in UNREACHABLE_CODES:
            _unreachable_until[where] = time.time() + UNREACHABLE_BACKOFF
        raise
    _unreachable_until.pop(where, None)
    with conn.cursor() as cur:
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        try:
            cur.execute(f"SET SESSION MAX_EXECUTION_TIME={QUERY_TIMEOUT_MS}")
        except pymysql.MySQLError:
            pass
    return conn


# Opening a connection to a remote database costs most of a second, so reuse them.
_pool = {}
_pool_lock = threading.Lock()


def _pool_key(cfg):
    return (cfg["host"], int(cfg["port"]), cfg["user"], cfg["password"], cfg["database"])


def _acquire(cfg):
    key = _pool_key(cfg)
    now = time.time()
    with _pool_lock:
        idle = _pool.setdefault(key, [])
        while idle:
            conn, last_used = idle.pop()
            if now - last_used < POOL_IDLE_SECONDS and conn.open:
                return conn
            _close(conn)
    return connect(cfg=cfg)


def _release(cfg, conn):
    with _pool_lock:
        idle = _pool.setdefault(_pool_key(cfg), [])
        if conn.open and len(idle) < POOL_SIZE:
            idle.append((conn, time.time()))
            return
    _close(conn)


def _close(conn):
    try:
        conn.close()
    except Exception:
        pass


def close_pool():
    with _pool_lock:
        for idle in _pool.values():
            for conn, _ in idle:
                _close(conn)
        _pool.clear()


def _clean_value(v):
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return v.hex()
    if isinstance(v, dt.timedelta):
        return str(v)
    return v


def run(sql, params=None, max_rows=MAX_ROWS, name=None, cfg=None):
    """Run a read query on a named connection. Returns (DataFrame, seconds, truncated)."""
    ready = check_read_only(sql)
    cfg = cfg or load_config(name)
    started = time.time()
    for attempt in (1, 2):
        conn = _acquire(cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(ready, params)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchmany(max_rows + 1)
                if len(rows) > max_rows:
                    cur.fetchall()  # drain the rest so the connection can be reused
        except pymysql.MySQLError as e:
            code = e.args[0] if e.args else 0
            _close(conn)
            if attempt == 1 and code in RECONNECT_CODES:
                continue  # a pooled connection had gone stale; try once on a fresh one
            raise
        except Exception:
            _close(conn)
            raise
        _release(cfg, conn)
        break
    truncated = len(rows) > max_rows
    rows = [[_clean_value(v) for v in r] for r in rows[:max_rows]]
    df = pd.DataFrame(rows, columns=_unique(cols))
    return df, time.time() - started, truncated


def _unique(cols):
    seen, out = {}, []
    for c in cols:
        if c in seen:
            seen[c] += 1
            out.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 0
            out.append(c)
    return out


def quote_ident(name):
    if not re.fullmatch(r"[A-Za-z0-9_$]+", name or ""):
        raise ReadOnlyError(f"“{name}” is not a valid table or column name.")
    return f"`{name}`"


def ping(name=None, cfg=None):
    df, secs, _ = run("SELECT VERSION() AS version, DATABASE() AS db, NOW() AS server_time",
                      name=name, cfg=cfg)
    return df.iloc[0].to_dict(), secs
