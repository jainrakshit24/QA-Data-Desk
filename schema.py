"""Database structure: tables, columns, links between them — cached locally."""
import json
import os
import re
import time

import pandas as pd

import db
import packs

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")
CACHE_MAX_AGE = 12 * 3600   # also refreshed automatically when a query hits a missing table/column

SIGNATURE_SQL = """SELECT COUNT(*) AS n, SUM(CRC32(CONCAT(TABLE_NAME, '.', COLUMN_NAME, ' ', COLUMN_TYPE))) AS h
                   FROM information_schema.columns WHERE table_schema = DATABASE()"""


def signature(name):
    """A cheap (~0.1s) fingerprint of every table and column; changes when the structure changes."""
    df, _, _ = db.run(SIGNATURE_SQL, name=name)
    return f"{int(df.iloc[0, 0] or 0)}:{int(df.iloc[0, 1] or 0)}"


def _fetch(name):
    tables, _, _ = db.run("""
        SELECT TABLE_NAME AS name, TABLE_ROWS AS rows_est, TABLE_TYPE AS kind,
               TABLE_COMMENT AS comment, UPDATE_TIME AS updated
        FROM information_schema.tables WHERE table_schema = DATABASE()
        ORDER BY TABLE_NAME""", name=name)
    cols, _, _ = db.run("""
        SELECT TABLE_NAME AS tbl, COLUMN_NAME AS col, COLUMN_TYPE AS type,
               IS_NULLABLE AS nullable, COLUMN_KEY AS `key`, COLUMN_DEFAULT AS `default`,
               EXTRA AS extra, COLUMN_COMMENT AS comment, ORDINAL_POSITION AS pos
        FROM information_schema.columns WHERE table_schema = DATABASE()
        ORDER BY TABLE_NAME, ORDINAL_POSITION""", max_rows=300000, name=name)
    fks, _, _ = db.run("""
        SELECT TABLE_NAME AS tbl, COLUMN_NAME AS col,
               REFERENCED_TABLE_NAME AS ref_tbl, REFERENCED_COLUMN_NAME AS ref_col
        FROM information_schema.key_column_usage
        WHERE table_schema = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL""", name=name)
    return {
        "fetched_at": time.time(),
        "signature": signature(name),
        "tables": tables.astype({"updated": str}).to_dict("records"),
        "columns": cols.to_dict("records"),
        "fks": fks.to_dict("records"),
    }


class Schema:
    def __init__(self, raw, connection=None):
        self.connection = connection
        self.fetched_at = raw["fetched_at"]
        self.signature = raw.get("signature")
        self.tables = pd.DataFrame(raw["tables"])
        self.tables["rows_est"] = pd.to_numeric(self.tables["rows_est"], errors="coerce").fillna(0).astype(int)
        self.columns = pd.DataFrame(raw["columns"])
        self.fks = pd.DataFrame(raw["fks"], columns=["tbl", "col", "ref_tbl", "ref_col"])
        self.names = set(self.tables["name"])
        self.cols_by_table = {t: g["col"].tolist() for t, g in self.columns.groupby("tbl")}
        # Precomputed once, so searching and opening tables stays fast on every click.
        self._frames = {t: g.sort_values("pos") for t, g in self.columns.groupby("tbl")}
        self._names_lower = self.tables["name"].str.lower()
        self._colnames_lower = self.tables["name"].map(lambda n: " ".join(self.cols_by_table.get(n, [])).lower())
        self.rows_est = dict(zip(self.tables["name"], self.tables["rows_est"]))
        self.key_of = {(t, c): k for t, c, k in zip(self.columns["tbl"], self.columns["col"], self.columns["key"])}
        self.tables_per_column = self.columns.groupby("col")["tbl"].nunique().sort_values(ascending=False)
        self._fk_by_col = {(r.tbl, r.col): (r.ref_tbl, r.ref_col) for r in self.fks.itertuples()}
        self._memo = {}
        self.reload_links()

    def reload_links(self):
        cfg = packs.load_links()
        split = lambda v: tuple(str(v).split(".", 1)) if "." in str(v) else None
        self.column_links = {k: split(v) for k, v in cfg["column_links"].items() if split(v)}
        self.table_links = {tuple(k.split(".", 1)): split(v) for k, v in cfg["table_links"].items()
                            if "." in k and split(v)}
        self.aliases = {k.lower(): [str(x).lower() for x in (v or [])] for k, v in cfg["aliases"].items()}
        self.topics = {k.lower(): list(v or []) for k, v in cfg["topics"].items()}
        self._memo = {}

    # ------------------------------------------------------------ lookups
    def has(self, table, column=None):
        if table not in self.names:
            return False
        return column is None or column in self.cols_by_table.get(table, [])

    def table_columns(self, table):
        return self._frames.get(table, self.columns.head(0))

    def find_tables(self, term, limit=60):
        """Tables matching a loose description, best first.

        Every word is expanded through the search aliases, then each table scores points for
        words found in its name (most) or in its column names (less). Tables that
        match every word rank above tables that match only some.
        """
        term = (term or "").strip().lower()
        key = ("find", term, limit)
        if key not in self._memo:
            self._memo[key] = self._find_tables(term, limit)
        return self._memo[key]

    def _find_tables(self, term, limit):
        t = self.tables.copy()
        if not term:
            return t.sort_values("rows_est", ascending=False).head(limit).assign(score=0.0, matched="")
        exact = term.replace(" ", "_")
        words = [w for w in re.split(r"[\s_.]+", term) if w]
        groups = [[w] + self.aliases.get(w, []) for w in words]
        name = self._names_lower
        colnames = self._colnames_lower
        score = pd.Series(0.0, index=t.index)
        hits = pd.Series(0, index=t.index)
        for g in groups:
            # short words (2-3 letters) must be a whole word in the name, not a fragment of one
            parts = [(r"(?:^|_)" + re.escape(x) + r"(?:_|s?$)") if len(x) <= 3 else re.escape(x) for x in g]
            pat = "|".join(parts)
            in_name = name.str.contains(pat)
            in_cols = colnames.str.contains(r"(?:^|\s|_)(?:" + pat + r")")
            score += in_name * 10 + (~in_name & in_cols) * 2
            hits += (in_name | in_cols).astype(int)
        score += (hits == len(groups)) * 25
        score += (name == exact) * 100 + name.str.startswith(exact) * 30
        score -= name.str.len() * 0.05
        pinned = [tb for phrase, tbs in self.topics.items()
                  if re.search(r"(?:^|\s)" + re.escape(phrase) + r"(?:$|\s)", term) for tb in tbs]
        for rank, tb in enumerate(dict.fromkeys(pinned)):
            idx = t.index[name == tb]
            score.loc[idx] += 500 - rank
            hits.loc[idx] = hits.loc[idx].clip(lower=1)
        t["score"] = score
        t["matched"] = hits.astype(str) + "/" + str(len(groups))
        t = t[hits > 0]
        return t.sort_values(["score", "rows_est"], ascending=False).head(limit)

    def find_columns(self, term, limit=300):
        """Every table that has a column whose name matches."""
        term = (term or "").strip().lower()
        if not term:
            return self.columns.head(0)
        c = self.columns[self.columns["col"].str.lower().str.contains(re.escape(term))]
        return c.head(limit)

    # ------------------------------------------------------------ links
    def link_for(self, table, column):
        """Where a column points: (target_table, target_column, how) or None."""
        fk = self._fk_by_col.get((table, column))
        if fk:
            return fk[0], fk[1], "foreign key"
        target = self.table_links.get((table, column)) or self.column_links.get(column)
        if target and target[0] != table and self.has(*target):
            return target[0], target[1], "configured link"
        guess = self._guess(table, column)
        if guess:
            return guess[0], guess[1], "name pattern"
        return None

    def _guess(self, table, column):
        """<name>_id -> a table called <name>, <name>s, <name>es or <name→ies> with an id column."""
        if not column.endswith("_id") or len(column) <= 3:
            return None
        base = column[:-3]
        options = [base, base + "s", base + "es"]
        if base.endswith("y"):
            options.append(base[:-1] + "ies")
        for name in options:
            if name != table and self.has(name, "id"):
                return name, "id"
        return None

    def outgoing(self, table):
        key = ("out", table)
        if key not in self._memo:
            self._memo[key] = self._outgoing(table)
        return self._memo[key]

    def _outgoing(self, table):
        rows = []
        for col in self.cols_by_table.get(table, []):
            link = self.link_for(table, col)
            if link:
                rows.append({"column": col, "points_to": f"{link[0]}.{link[1]}",
                             "table": link[0], "target_col": link[1], "how": link[2]})
        return pd.DataFrame(rows, columns=["column", "points_to", "table", "target_col", "how"])

    def incoming(self, table):
        """Other tables whose columns point at this table (foreign keys, configured links, name pattern)."""
        key = ("in", table)
        if key not in self._memo:
            self._memo[key] = self._incoming(table)
        return self._memo[key]

    def _incoming(self, table):
        rows = []
        for r in self.fks[self.fks["ref_tbl"] == table].itertuples():
            rows.append((r.tbl, r.col, r.ref_col, "foreign key"))
        for col, tgt in self.column_links.items():
            if tgt[0] == table and self.has(table, tgt[1]):
                for t in self.columns.loc[(self.columns["col"] == col) & (self.columns["tbl"] != table), "tbl"]:
                    if self.table_links.get((t, col), tgt)[0] == table:
                        rows.append((t, col, tgt[1], "configured link"))
        for (t, col), tgt in self.table_links.items():
            if tgt[0] == table and self.has(t, col) and self.has(table, tgt[1]):
                rows.append((t, col, tgt[1], "configured link"))
        if self.has(table, "id"):
            stems = {table, table[:-1] if table.endswith("s") else table, table[:-2] if table.endswith("es") else table}
            if table.endswith("ies"):
                stems.add(table[:-3] + "y")
            wanted = {f"{s}_id" for s in stems if s}
            hit = self.columns[self.columns["col"].isin(wanted) & (self.columns["tbl"] != table)]
            for r in hit.itertuples():
                if self.link_for(r.tbl, r.col) and self.link_for(r.tbl, r.col)[0] == table:
                    rows.append((r.tbl, r.col, "id", "name pattern"))
        df = pd.DataFrame(rows, columns=["table", "column", "target_col", "how"])
        df["matches"] = table + "." + df["target_col"]
        return df.drop_duplicates(["table", "column"])[["table", "column", "matches", "target_col", "how"]]

    def compact(self, tables, max_cols=60):
        """Short text description of tables, for the AI prompt."""
        out = []
        for t in tables:
            cols = self.table_columns(t)
            spec = ", ".join(f"{r.col} {r.type}" for r in cols.head(max_cols).itertuples())
            more = f", …(+{len(cols) - max_cols} more)" if len(cols) > max_cols else ""
            links = self.outgoing(t)
            link_txt = ("; links: " + ", ".join(f"{r.column}->{r.points_to}" for r in links.itertuples())) if len(links) else ""
            est = int(self.tables.loc[self.tables["name"] == t, "rows_est"].iloc[0]) if t in self.names else 0
            out.append(f"{t} (~{est} rows): {spec}{more}{link_txt}")
        return "\n".join(out)


def _cache_path(name):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "default")
    return os.path.join(CACHE_DIR, f"schema_{safe}.json")


def load(name=None, force=False):
    """Schema for one connection, from a local cache refreshed once a day."""
    name = name or db.load_config()["name"]
    path = _cache_path(name)
    if not force and os.path.exists(path):
        with open(path) as f:
            raw = json.load(f)
        if time.time() - raw.get("fetched_at", 0) < CACHE_MAX_AGE:
            return Schema(raw, name)
    raw = _fetch(name)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(raw, f, default=str)
    return Schema(raw, name)
