"""Build a record timeline from timestamp columns that really exist in fetched rows.

Nothing is inferred: every event is one (table, row, timestamp column, value) found in the data.
"""
import datetime as dt
import re

import pandas as pd

TIME_TYPES = ("datetime", "timestamp", "date")
TIME_NAME = re.compile(r"(_at|_on|_date|_time|_datetime)$|^(created|updated|modified|timestamp|date)$", re.I)
STATUS_NAME = re.compile(r"(^|_)(status|state|stage)$", re.I)


def is_time_column(name, col_type=""):
    t = (col_type or "").lower()
    if any(t.startswith(x) for x in TIME_TYPES):
        return True
    if t and not any(x in t for x in ("char", "text", "int")):
        return False
    return bool(TIME_NAME.search(name)) and not t.startswith(("tinyint", "smallint"))


def _to_time(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.to_pydatetime()
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float)):
        if 946684800 <= v <= 4102444800:          # 2000-01-01 .. 2100-01-01 as unix seconds
            return dt.datetime.utcfromtimestamp(v)
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s or s.startswith("0000-00-00"):
            return None
        try:
            return pd.to_datetime(s).to_pydatetime()
        except (ValueError, TypeError):
            return None
    return None


def build_events(sources, column_types=None, id_columns=("id", "uid")):
    """sources: list of (table, DataFrame). column_types: {(table, column): mysql type}.

    Returns a DataFrame sorted by time with columns:
    time, table, row, column, event, status
    """
    column_types = column_types or {}
    rows = []
    for table, df in sources:
        if df is None or df.empty:
            continue
        time_cols = [c for c in df.columns if is_time_column(c, column_types.get((table, c), ""))]
        status_cols = [c for c in df.columns if STATUS_NAME.search(str(c))]
        id_col = next((c for c in id_columns if c in df.columns), None)
        for i, rec in df.reset_index(drop=True).iterrows():
            row_label = f"{id_col}={rec[id_col]}" if id_col else f"row {i + 1}"
            status = ", ".join(f"{c}={rec[c]}" for c in status_cols if pd.notna(rec[c]))
            for c in time_cols:
                when = _to_time(rec[c])
                if when is None:
                    continue
                rows.append({"time": when, "table": table, "row": row_label, "column": c,
                             "event": f"{table}.{c}", "status": status})
    out = pd.DataFrame(rows, columns=["time", "table", "row", "column", "event", "status"])
    return out.sort_values(["time", "table", "column"], kind="stable").reset_index(drop=True)
