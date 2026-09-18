# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Keeps secrets and personal data out of what the dashboard shows.

- Secret columns (passwords, tokens, OTPs, API keys) are never shown to anyone,
  and a query that names one is refused.
- Email addresses and phone numbers are masked for everyone except admins.
"""
import re

import pandas as pd

BASE_BLOCKED = ["password", "passwd", "pw_hash", "password_hash", "secret", "client_secret", "api_key", "apikey",
                "access_token", "refresh_token", "auth_token", "authorization", "otp", "otp_code", "aadhaar", "aadhar",
                "aadhaar_no", "pan_number", "pan_no"]
BASE_SECRET_PARTS = ["password", "passwd", "pw_hash", "secret", "api_key", "apikey", "token", "otp", "authorization",
                     "aadhaar", "aadhar", "pan"]
BASE_PERSONAL_PARTS = ["mobile", "phone", "contact", "whatsapp"]
NAME_RE = re.compile(r"^[A-Za-z0-9_]{2,64}$")

EXTRA_SECRET = []      # set by configure(); admin-managed names
EXTRA_PERSONAL = []


def _alt(words):
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))


def configure(extra_secret=(), extra_personal=()):
    """Add admin-configured column names on top of the built-in lists. Invalid names are ignored."""
    global BLOCKED_WORDS, SECRET_COLUMN, PHONE_COLUMN, EXTRA_SECRET, EXTRA_PERSONAL
    global _SENSITIVE_NAME, _SENSITIVE_COMPARE, _SENSITIVE_IN
    EXTRA_SECRET = [w.lower() for w in extra_secret if NAME_RE.match(w or "")]
    EXTRA_PERSONAL = [w.lower() for w in extra_personal if NAME_RE.match(w or "")]
    BLOCKED_WORDS = re.compile(rf"\b({_alt(BASE_BLOCKED + EXTRA_SECRET)})\b", re.I)
    SECRET_COLUMN = re.compile(rf"(^|_)({_alt(BASE_SECRET_PARTS + EXTRA_SECRET)})(_|$)", re.I)
    PHONE_COLUMN = re.compile(rf"(^|_)({_alt(BASE_PERSONAL_PARTS + EXTRA_PERSONAL)})(_|\d|$)", re.I)
    _SENSITIVE_NAME = (rf"\b[\w.`]*(?:{_alt(BASE_PERSONAL_PARTS + BASE_SECRET_PARTS + ['passwd'] + EXTRA_SECRET + EXTRA_PERSONAL)})"
                       r"[\w`]*")
    _SENSITIVE_COMPARE = re.compile(rf"(?i)({_SENSITIVE_NAME}\s*(?:=|<>|!=|like)\s*)({_LITERAL})")
    _SENSITIVE_IN = re.compile(rf"(?i)({_SENSITIVE_NAME}\s+(?:not\s+)?in\s*\()([^)]*)\)")


def parse_names(text):
    """Comma / newline separated names → (valid, invalid)."""
    items = [w.strip() for w in re.split(r"[,\n]", text or "") if w.strip()]
    return [w for w in items if NAME_RE.match(w)], [w for w in items if not NAME_RE.match(w)]


EMAIL_VALUE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
HIDDEN = "•••• hidden"


class PrivacyError(ValueError):
    """Raised when a query asks for secret columns."""


def _without_strings(sql):
    sql = re.sub(r"'(?:[^'\\]|\\.)*'", "''", sql)
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', sql)


_LITERAL = r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|\d{4,}"
_EMAIL_IN_TEXT = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
configure()


def scrub_sql(sql):
    """SQL text safe to keep in history: email addresses and values compared with phone / secret columns are masked."""
    if not sql:
        return sql
    text = _SENSITIVE_IN.sub(lambda m: m.group(1) + re.sub(_LITERAL, "'•••'", m.group(2)) + ")", sql)
    text = _SENSITIVE_COMPARE.sub(lambda m: m.group(1) + "'•••'", text)
    return _EMAIL_IN_TEXT.sub(lambda m: m.group(1) + "•••" + m.group(2), text)


def check_sql(sql):
    hit = BLOCKED_WORDS.search(_without_strings(sql))
    if hit:
        raise PrivacyError(
            f"Queries that use “{hit.group(0)}” are blocked — secret fields are never shown in this app."
        )


def is_secret_column(name):
    return bool(SECRET_COLUMN.search(str(name)))


def _mask_phone(v):
    if not isinstance(v, str) and not isinstance(v, int):
        return v
    s = str(v)
    digits = re.sub(r"\D", "", s)
    if len(digits) < 7:
        return v
    return digits[:2] + "•" * (len(digits) - 4) + digits[-2:]


def _mask_emails(v):
    if isinstance(v, str) and "@" in v:
        return EMAIL_VALUE.sub(lambda m: m.group(1) + "•••" + m.group(2), v)
    return v


def mask(df, is_admin=False):
    """Return a copy that is safe to show on screen or download."""
    if df is None or df.empty and not len(df.columns):
        return df
    out = df.copy()
    for col in out.columns:
        if is_secret_column(col):
            out[col] = HIDDEN
            continue
        if is_admin:
            continue
        if PHONE_COLUMN.search(str(col)):
            out[col] = out[col].map(_mask_phone)
        if out[col].dtype == object:
            out[col] = out[col].map(_mask_emails)
    return out


def safe_columns(columns):
    return [c for c in columns if not is_secret_column(c)]


def masked_columns(df, is_admin=False):
    """Names of the columns that were hidden or masked, for a caption."""
    if df is None:
        return []
    names = [c for c in df.columns if is_secret_column(c)]
    if not is_admin:
        names += [c for c in df.columns if PHONE_COLUMN.search(str(c)) and not is_secret_column(c)]
    return names


def contains_personal_data(df):
    if df is None or df.empty:
        return False
    sample = df.head(200)
    for col in sample.columns:
        if sample[col].dtype == object and sample[col].astype(str).str.contains("@", regex=False).any():
            return True
    return any(PHONE_COLUMN.search(str(c)) for c in df.columns)


__all__ = ["PrivacyError", "check_sql", "mask", "safe_columns", "is_secret_column",
           "masked_columns", "contains_personal_data", "HIDDEN", "pd"]
