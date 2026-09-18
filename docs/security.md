# Security and privacy

This page is written so you can hand it to whoever owns the database and have a short conversation.

## What the app is allowed to do

It opens a MySQL connection with the credentials you give it and runs `SELECT`-shaped statements. That is all.
There is no code path that writes, and two independent layers stop one from existing by accident.

### Layer 1 — the statement guard (`db.check_read_only`)

Runs before every query, including ones written by AI, loaded from YAML, or typed by an admin.

| Rejected | Why it matters |
|---|---|
| Anything not starting with `SELECT`, `SHOW`, `DESCRIBE`, `EXPLAIN`, `WITH`, `TABLE` | the obvious case |
| `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `RENAME`, `GRANT`, `REVOKE` anywhere outside a string | MySQL 8 allows `WITH … UPDATE`, so checking the first word is not enough |
| `/*! … */` executable comments | MySQL runs the contents; ordinary comment stripping would miss it |
| `INTO OUTFILE`, `INTO DUMPFILE` | writes to the server's filesystem |
| `FOR UPDATE`, `FOR SHARE`, `LOCK IN SHARE MODE` | locking reads can block production traffic |
| `LOAD_FILE`, `GET_LOCK`, `RELEASE_LOCK`, `SLEEP` and friends | file access and lock abuse |
| More than one statement in one call | stops `SELECT 1; DROP TABLE x` |

`tests/test_security.py` throws 81+ bypass attempts at this on every commit.

### Layer 2 — the database session

Every pooled connection is opened with `SET SESSION TRANSACTION READ ONLY` and `autocommit`. If the guard were
ever wrong, MySQL refuses the write itself with error 1792. A live test proves this against the real database:
it deliberately bypasses the guard and asserts MySQL says no.

**Recommended anyway:** give the app a database user with `SELECT` permission only, so there are three layers.
See [Setup, step 1](../SETUP.md#ask-for-a-read-only-database-user).

---

## What it does with data it reads

### Secret columns are never selected
Columns whose names contain `password`, `passwd`, `pw_hash`, `secret`, `api_key`, `token`, `otp`, `authorization`,
`aadhaar`, `pan` (and anything an admin adds on **Admin → Privacy**) are refused at query time — a query that names
one is rejected, and if such a column arrives through `SELECT *` its values are replaced with `•••• hidden` for
everyone, admins included.

### Personal data is masked for non-admins
Columns whose names contain `mobile`, `phone`, `contact`, `whatsapp` are masked (`98••••••10`), and anything that
looks like an email address is masked wherever it appears in a value (`f•••@example.com`). Admins see the real
values on screen; **evidence reports and downloads are masked for everyone**, because those leave the building.

### Logs keep the shape of a query, not its secrets
The activity log stores the SQL text with sensitive literals scrubbed: values compared against a sensitive column,
including `IN (…)` lists, and any email-looking string are replaced before the row is written. Row counts, duration,
connection name and error messages are kept; rows are not.

### Nothing that reads rows stores rows
| Feature | What it stores |
|---|---|
| QA checks and the dashboard | the result **count** per run, plus status and duration |
| Consistency rules, playbooks, saved queries | the definition and the SQL, never the results |
| Schema snapshots | table and column structure only |
| Record snapshots | masked values **plus** an HMAC-SHA256 fingerprint of the real value, keyed by a local secret (`.snapshot_key`, mode 600), so a change to a hidden field is detectable without keeping the value |
| Evidence basket | lives in your browser session until you export it — already masked |

---

## The AI boundary

AI is optional and off until an admin adds a Gemini key.

**Can be sent:** table names, column names and types; your question or bug description after scrubbing emails,
phones and secrets; SQL text you are asking about; the domain notes you wrote in `ai_context.md`.

**Never sent:** database rows, cell values, the identifier value you searched for, credentials, or anything from a
result set.

**Never executed:** SQL that comes back is text. It is validated by the same read-only guard, shown to you, and runs
only when you press Run. The same applies to AI-suggested test data — it is labelled `NOT EXECUTED` and this app
cannot run it.

On Google's free tier, prompts may be used to improve Google's models. Keep company-specific notes in
`local/ai_context.md` (git-ignored) rather than `config/ai_context.md` if that matters to you.

---

## Accounts and access

- The first account needs a one-time setup code, so an exposed instance cannot be claimed by a stranger.
- Later sign-ups are inactive until an admin approves them.
- Passwords need 10+ characters with a letter and a number, and are stored as salted scrypt hashes (n=16384, r=8, p=1).
- Five failed attempts lock an account for 15 minutes.
- A disabled account is signed out on its next interaction, not at the next login.
- Admin-only: managing people, connections, the AI key, privacy names, and the activity log.

---

## Limits that protect the database

| Limit | Value | Why |
|---|---|---|
| Statement timeout | 60 s (`MAX_EXECUTION_TIME`) | one bad query cannot pin a connection |
| Rows per query | 20,000 | protects the browser and the network |
| Automatic `LIMIT` | added when a query has none | a forgotten filter costs a second |
| Connection pool | 4 idle per database, 5 min | avoids reconnect storms |
| Unreachable host | fail fast for 20 s | one VPN drop does not hang every page |
| Cross-table search | capped, batched, indexed columns first | a 1,300-table database stays usable |

---

## What is never published to git

`.env`, `.db.json`, `.ai.json`, `app_data.sqlite3`, `.setup_code`, `.snapshot_key`, `cache/`, `logs/` and the whole
`local/` pack are git-ignored. The repository contains the application and a generic `config/` pack only — no
hostnames, no credentials, no company-specific checks. Verify any time with:

```bash
git ls-files | grep -E "\.env$|\.db\.json$|\.ai\.json$|^local/|^cache/|sqlite3"   # should print nothing
```

---

## Reporting a problem

If you find a way to write to a database, read a secret column, or get data into an AI prompt, please open an issue
with the steps — and never paste real credentials or real rows into it.

---

© 2026 Rakshit Jain · MIT licensed.
