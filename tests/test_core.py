# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Core tests. They need no database, except the live checks at the bottom,
which run only when a connection is configured.

Run:  python3 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp()
os.environ["QA_APP_DB"] = os.path.join(TMP, "test.sqlite3")

import pandas as pd  # noqa: E402

import auth  # noqa: E402
import db  # noqa: E402
import packs  # noqa: E402
import privacy  # noqa: E402
import schema  # noqa: E402


class ReadOnlyGuard(unittest.TestCase):
    allowed = [
        "SELECT id, updated, created FROM t LIMIT 1",
        "select * from t where start_datetime < now()",
        "SELECT 'update me; delete' AS s",
        "SHOW TABLES LIKE 'x%'",
        "WITH x AS (SELECT 1 a) SELECT * FROM x",
        "EXPLAIN SELECT 1",
    ]
    blocked = [
        "UPDATE t SET a=1",
        "delete from t",
        "WITH x AS (SELECT 1) UPDATE t SET a=1",
        "SELECT 1; DROP TABLE t",
        "SELECT * FROM t INTO OUTFILE '/tmp/x'",
        "/* hi */ INSERT INTO t VALUES (1)",
        "CREATE TABLE x (a int)",
        "",
    ]

    def test_reads_allowed(self):
        for q in self.allowed:
            with self.subTest(q=q):
                db.check_read_only(q)

    def test_writes_blocked(self):
        for q in self.blocked:
            with self.subTest(q=q), self.assertRaises(db.ReadOnlyError):
                db.check_read_only(q)

    def test_identifier_quoting(self):
        self.assertEqual(db.quote_ident("user_id"), "`user_id`")
        with self.assertRaises(db.ReadOnlyError):
            db.quote_ident("id`; DROP")


class Privacy(unittest.TestCase):
    def test_secret_words_blocked(self):
        for q in ["SELECT password FROM users", "SELECT api_key FROM x", "select otp from user_otp_log"]:
            with self.subTest(q=q), self.assertRaises(privacy.PrivacyError):
                privacy.check_sql(q)
        privacy.check_sql("SELECT * FROM user_otp_log WHERE note = 'password'")

    def test_masking(self):
        df = pd.DataFrame({"uid": [1], "email": ["someone@example.com"], "mobile_number": ["9876543210"],
                           "password": ["hash"], "api_token": ["t"]})
        user_view = privacy.mask(df, is_admin=False).iloc[0]
        self.assertEqual(user_view["password"], privacy.HIDDEN)
        self.assertEqual(user_view["api_token"], privacy.HIDDEN)
        self.assertNotIn("someone", user_view["email"])
        self.assertTrue(user_view["mobile_number"].startswith("98") and "•" in user_view["mobile_number"])
        admin_view = privacy.mask(df, is_admin=True).iloc[0]
        self.assertEqual(admin_view["email"], "someone@example.com")
        self.assertEqual(admin_view["password"], privacy.HIDDEN)


class Accounts(unittest.TestCase):
    def setUp(self):
        if os.path.exists(os.environ["QA_APP_DB"]):
            os.remove(os.environ["QA_APP_DB"])
        auth.init()

    def test_first_admin_needs_setup_code(self):
        ok, _ = auth.sign_up("owner", "Owner", "o@example.com", "Password1234", code="WRONG")
        self.assertFalse(ok)
        ok, _ = auth.sign_up("owner", "Owner", "o@example.com", "Password1234", code="testcode")
        self.assertTrue(ok)
        user, _ = auth.sign_in("owner", "Password1234")
        self.assertEqual(user["role"], "admin")

    def test_new_accounts_wait_for_approval_and_lock_out(self):
        auth.sign_up("owner", "Owner", "o@example.com", "Password1234", code="TESTCODE")
        ok, _ = auth.sign_up("guest", "Guest", "g@example.com", "Password1234")
        self.assertTrue(ok)
        user, msg = auth.sign_in("guest", "Password1234")
        self.assertIsNone(user)
        self.assertIn("approval", msg)
        guest = [u for u in auth.list_users() if u["username"] == "guest"][0]
        auth.set_status(guest["id"], "active")
        self.assertIsNotNone(auth.sign_in("guest", "Password1234")[0])
        for _ in range(auth.MAX_FAILED):
            auth.sign_in("guest", "wrong-password1")
        user, msg = auth.sign_in("guest", "Password1234")
        self.assertIsNone(user)
        self.assertIn("Too many failed attempts", msg)

    def test_weak_passwords_and_bad_usernames(self):
        self.assertFalse(auth.sign_up("ab", "A", "a@example.com", "Password1234", code="TESTCODE")[0])
        self.assertFalse(auth.sign_up("abc", "A", "a@example.com", "short1", code="TESTCODE")[0])
        self.assertFalse(auth.sign_up("abc", "A", "a@example.com", "nodigitsatall", code="TESTCODE")[0])


class Schema(unittest.TestCase):
    """A fake database structure — no connection needed."""

    def setUp(self):
        cols = [
            ("customers", "id", "PRI"), ("customers", "name", ""), ("customers", "password", ""),
            ("orders", "id", "PRI"), ("orders", "customer_id", "MUL"), ("orders", "category_id", ""),
            ("orders", "status", ""), ("categories", "id", "PRI"), ("order_items", "order_id", "MUL"),
            ("audit_log", "id", "PRI"),
        ]
        raw = {
            "fetched_at": 0,
            "tables": [{"name": t, "rows_est": 10, "kind": "BASE TABLE", "comment": "", "updated": ""}
                       for t in dict.fromkeys(c[0] for c in cols)],
            "columns": [{"tbl": t, "col": c, "type": "int", "nullable": "YES", "key": k, "default": None,
                         "extra": "", "comment": "", "pos": i} for i, (t, c, k) in enumerate(cols)],
            "fks": [],
        }
        self.s = schema.Schema(raw, "fake")

    def test_name_pattern_links(self):
        self.assertEqual(self.s.link_for("orders", "customer_id")[:2], ("customers", "id"))
        self.assertEqual(self.s.link_for("orders", "category_id")[:2], ("categories", "id"))
        self.assertEqual(self.s.link_for("order_items", "order_id")[:2], ("orders", "id"))
        incoming = self.s.incoming("orders")
        self.assertIn("order_items", incoming["table"].tolist())

    def test_search(self):
        self.assertEqual(self.s.find_tables("order")["name"].iloc[0], "orders")
        self.assertIn("audit_log", self.s.find_tables("audit")["name"].tolist())
        self.assertIn("orders", self.s.find_columns("customer")["tbl"].tolist())


class Packs(unittest.TestCase):
    def test_local_overrides_config(self):
        os.makedirs(packs.LOCAL_DIR, exist_ok=True)
        with open(os.path.join(packs.LOCAL_DIR, "checks.yaml"), "w") as f:
            f.write("- id: any_largest_tables\n  title: Replaced locally\n  sql: SELECT 1\n"
                    "- id: my_check\n  title: Mine\n  sql: SELECT 2\n")
        items = {c["id"]: c for c in packs.load_list("checks")}
        self.assertEqual(items["any_largest_tables"]["title"], "Replaced locally")
        self.assertIn("my_check", items)

    def test_shipped_config_is_valid(self):
        for name in ("checks", "charts"):
            for item in packs.load_list(name):
                with self.subTest(item=item.get("id")):
                    db.check_read_only(item["sql"])
                    privacy.check_sql(item["sql"])


@unittest.skipUnless(os.path.exists(os.path.join(HERE, ".db.json")) or os.environ.get("QA_DB_HOST"),
                     "no database configured")
class LiveDatabase(unittest.TestCase):
    def test_session_is_read_only(self):
        """Bypass the text guard on purpose: MySQL itself must refuse a write to a real table."""
        import pymysql
        df, _, _ = db.run("SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.columns "
                          "WHERE table_schema = DATABASE() LIMIT 1")
        table, column = df.iloc[0, 0], df.iloc[0, 1]
        conn = db.connect()
        try:
            with conn.cursor() as cur, self.assertRaises(pymysql.MySQLError) as ctx:
                cur.execute(f"UPDATE `{table}` SET `{column}` = `{column}` WHERE 1 = 0")
            self.assertEqual(ctx.exception.args[0], 1792)  # cannot execute in a READ ONLY transaction
        finally:
            conn.rollback()
            conn.close()

    def test_ping(self):
        info, _ = db.ping()
        self.assertTrue(info["version"])


if __name__ == "__main__":
    unittest.main()
