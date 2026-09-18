# Architecture — how it works

Python + Streamlit, no build step and no JavaScript of its own. One process serves the UI; every page is a function.

```
browser
  │
  ├── app.py ......... sign-in gate, navigation, sidebar (database picker, global search)
  │     └── views/*.py ... one file per page — layout and interaction only
  │           └── core/*.py ... the logic, with no Streamlit import — tested directly
  │
  ├── ui.py .......... shared plumbing: run a query, show a result, errors, evidence basket,
  │                    session-state helpers, schema cache access
  ├── db.py .......... connections, pooling, the read-only guard, row limits
  ├── privacy.py ..... what must never be selected, shown or logged
  ├── schema.py ...... the table/column cache and link detection
  ├── auth.py ........ accounts, saved queries, activity log      ┐ both in one SQLite file
  ├── store.py ....... check runs, rules, playbooks, snapshots    ┘ (app_data.sqlite3)
  ├── packs.py ....... merges config/ (generic, published) with local/ (yours, git-ignored)
  ├── ai.py .......... optional Gemini calls — names and SQL text only
  └── scheduler.py ... headless check runner for cron (no Streamlit)
```

The rule the layout enforces: **`core/` never imports Streamlit, `views/` never talks to the database directly.**
That is why the logic can be unit-tested without a browser and without a database.

---

## The path of a query

Every read follows the same path, whether it came from a button, a check, a playbook or the SQL editor:

1. **`db.check_read_only(sql)`** — a text guard. The statement must start with a read verb (`SELECT`, `SHOW`,
   `DESCRIBE`, `EXPLAIN`, `WITH`, `TABLE`); write words are refused anywhere outside string literals (MySQL 8
   allows `WITH … UPDATE`, so "starts with WITH" is not enough); executable comments `/*! … */`, `INTO OUTFILE`,
   locking reads (`FOR UPDATE`, `FOR SHARE`, `LOCK IN SHARE MODE`) and functions like `LOAD_FILE` or `GET_LOCK`
   are blocked. Multiple statements are refused.
2. **`privacy.check_sql(sql)`** — refuses queries that name a secret column at all.
3. **`db.apply_row_limit(sql, n)`** — adds `LIMIT n + 1` when the query has none, so a forgotten filter costs a
   second, not a minute.
4. **`db.run(...)`** — takes a pooled connection, which was opened with
   `SET SESSION TRANSACTION READ ONLY` and `MAX_EXECUTION_TIME = 60000`. If the guard were ever wrong, MySQL
   itself refuses the write with error 1792.
5. **`privacy.mask(df, is_admin)`** — before anything reaches the screen or a download.
6. **`auth.log_query(...)`** — the activity log stores the SQL with sensitive literals scrubbed, the row count, the
   duration and the connection. Never the rows.

Errors never propagate as stack traces: `ui.run_query` returns a result dict, and `core/errors.py` turns the MySQL
error code into a message written for a QA engineer, with likely reasons and what to try.

---

## Connections

`db.load_connections()` merges two sources: one connection from the environment (`QA_DB_*`, typically from `.env`)
and any number from `.db.json`, which the Databases page writes with mode `600`. Ports are always coerced to `int`
so a `.env` string and a file number can live in the same list.

A small pool keeps up to four idle connections per database for five minutes — the difference between 0.87 s and
0.08 s per query in practice. A dropped connection (2006/2013) is retried once. When a host is unreachable
(2002/2003/2005) the app fails fast for 20 seconds instead of making every page wait on a TCP timeout.

---

## The schema cache

Reading `information_schema` for a 1,300-table database is slow, so it is done once and cached per connection in
`cache/schema_<name>.json`. To know whether the cache is still true, the app runs a fingerprint query — a `COUNT`
and a `SUM(CRC32(...))` over table, column and type names — which takes about 0.1 s, and only re-reads everything
when the fingerprint changed. The check runs at most every ten minutes, and immediately when a query fails with
"unknown column" or "table doesn't exist".

The `Schema` object precomputes what pages ask for constantly: columns per table, row estimates, key types,
outgoing and incoming links, and memoised searches. Links come from three sources, in this order: real foreign
keys, the `links.yaml` you configure, and the `<name>_id → <name>.id` naming pattern — and a configured link is
only used when the target column actually exists.

---

## Where state lives

| State | Where | Notes |
|---|---|---|
| Accounts, saved queries, rule sets, activity log | `auth.py` tables in `app_data.sqlite3` | passwords hashed; SQL masked |
| Check runs, consistency rules, playbooks, schema snapshots, record snapshots, settings | `store.py` tables in the same file | counts and definitions only |
| Connections | `.db.json` (mode 600) or the environment | |
| Table and column lists | `cache/` | rebuilt on demand |
| Your checks, charts, links, notes, templates | `local/` | git-ignored |
| Anything you are in the middle of | Streamlit session state | per browser session |

Database **rows are never stored**. Check history keeps a count; record snapshots keep masked values plus an
HMAC-SHA256 fingerprint (keyed by `.snapshot_key`, mode 600) so a change in a hidden field is detectable without
the value; evidence lives in the session until you export it.

---

## The UI layer

A few Streamlit realities shaped the code:

- **Lazy tabs.** `st.tabs` runs every tab's body on every interaction. `ui.lazy_tabs` uses `on_change="rerun"` and
  `tab.open` so only the visible tab runs its queries. The cost: Streamlit drops the session state of widgets in
  closed tabs, which is why `ui.restore` / `ui.remember` / `ui.sticky_editor` exist.
- **Widgets cannot be changed after they render.** To set a value for the next run, pages use
  `ui.set_pending(key, value)` and call `ui.apply_pending(key)` before the widget is created. That is how
  "Open in SQL editor" and Search's **Open** land on a filled-in page.
- **Expanders do not keep their state across a rerun.** Anything inside one that triggers a rerun must pass an
  explicit `expanded=` computed from session state, or the form collapses under the user.
- **Navigation** is `st.navigation` with `expanded=True` (otherwise pages hide behind "View 10 more"), and
  `ui.go(page_key)` switches pages from a button.

---

## Parallelism

`ui.run_many` runs independent read queries in a thread pool (six at a time) on the same connection pool, and logs
them from the calling thread. It is what makes "search this id in 40 tables", "run 26 checks" and "count rows on
both environments" take seconds instead of a minute. Cross-table counting also batches statements into capped
`UNION ALL` queries rather than one round trip per table.

---

## The AI boundary

`ai.py` is the only module that talks to the outside world, and it can only send:

- table names, column names and types (`schema.compact`)
- your question or bug description, scrubbed of emails, phone numbers and secrets
- SQL text you are asking about
- your own notes from `config/ai_context.md` and `local/ai_context.md`

It never sends rows, never sends the identifier value you searched for, and never executes anything. SQL that comes
back is validated by the same read-only guard before it is even offered to you, and you press Run. Tests assert
each of those (`tests/test_investigation.py`, `tests/test_phase3.py`).

---

## Scheduling

`scheduler.py` deliberately does not import Streamlit. It loads the same YAML checks, runs them through `db.run`,
evaluates them with `core/checks.py`, and records every run with `store.record_check_run` — the same history the
dashboard reads. That is why a cron run and a button click are indistinguishable afterwards.

---

© 2026 Rakshit Jain · MIT licensed.
