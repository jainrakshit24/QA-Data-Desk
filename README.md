# QA Data Desk

**Investigate, validate and prove data issues.** A read-only workspace for QA engineers: start from a bug
or an identifier, trace the record through related tables, compare an API response with the database,
and turn what you found into a Jira-ready bug report — without writing SQL.

## The investigation flow

```
Bug or identifier ─► Start investigation ─► Investigate record ─► Validate API ─► Bug evidence
                         (search / AI plan)     (links, graph,        (API ↔ DB ↔        (Jira / Markdown
                                                 timeline)             expected, rules)    report, masked)
```

| Page | Use it to |
|---|---|
| **Start investigation** *(home)* | **Identifier:** search one value — ID, email, mobile, reference code — across every indexed identifier column in the database at once, see which tables and columns hold it, and open the record. **Bug description:** the AI proposes an investigation — stated facts, possible explanations (clearly marked unconfirmed), data paths, and SQL steps you review and run one by one. |
| **Investigate record** | **Snapshots:** save what a record looked like now and compare it with the live record later (values stored masked; a keyed fingerprint detects changes to masked fields). One record with its fields, every linked record (foreign keys, `local/links.yaml`, `<name>_id` pattern) with row counts, a relationship graph, and a timeline built only from timestamp columns that exist in the rows. Open linked records (with a breadcrumb back), preview them, or open a ready JOIN in the SQL editor. |
| **Validate API** | **API ↔ Database:** paste a response, pick the table and matching row, map fields (auto-mapped by name), optionally add expected values for a three-way check. Shows Match / Mismatch / Missing in API / Missing in DB and which layer is wrong. Normalises case, spaces, numbers, booleans, dates and NULLs (each switchable). **Response rules:** check the response on its own — equals, exists, not null, count, regex, type, status code — and save rule sets. |
| **Playbooks** | Saved investigations and templates: parameters (`:value` bound safely, `{{table}}` / `{{column}}` checked against the schema) plus ordered SQL steps with purpose and “look for”. Run step by step or all at once, duplicate, share, edit. Ships generic templates (duplicates, orphans, missing values, status breakdown, record neighbours); an AI investigation plan can be saved as a playbook. |
| **Bug evidence** | Everything collected with “Add to evidence”, turned into a Jira, Markdown or plain-text report from a template your team can edit. Always masked, even for admins. |
| **Data consistency** | **Rules:** “every `payments` row where status = SUCCESS must have a `leads` row with the same user_id” — shows checked / passed / failed with the failing rows, over all rows or only the newest N; save rules for the team or export one as a QA check. **Compare tables:** map fields between two tables joined on a key and see matching, mismatched, missing in either table and duplicates, with the same normalisation options as the API validator. |
| **Schema compare** | Compare two connections, or a saved snapshot against the live database: missing / extra tables and columns, type, nullability, index and default changes, foreign keys, and tables whose row counts moved a lot. Download TXT / CSV. |
| **QA dashboard** | Latest status of every check: run today, passed, failed, warnings, total issues, change since the previous run, trend per day, and a data quality report as TXT, CSV or XLSX. |
| **Search everything** | One box over tables, columns, saved queries, QA checks, playbooks, consistency rules and your query history. Open a result and it lands on the right page, already filled in. There is also a search box in the sidebar. |
| **Test ideas** | **Edge cases:** per column, worked out from its type, name, key and links — empty and NULL, length limits, number ranges, dates (leap day, 2038, timezone boundary), enum values, duplicates, orphan links and cross-column cases (end before start, active-but-deleted). Then count how many existing rows already hit them — counts only, never values. **Test data:** example rows as SQL or JSON, clearly labelled `NOT EXECUTED` (the app is read-only and cannot run them), plus read-only queries that find rows already in the state you need, and optional AI scenarios. |
| **Compare environments** | The same record, the same query or table row counts across two connections. Field-by-field differences (masked), rows missing on either side, and exact counts or fast estimates. |
| **Database health** | Structure-only findings: tables with no primary key or no index, link columns whose type differs from their target, unindexed link columns on large tables, mostly-nullable and very wide tables, empty tables and backup-looking leftovers. No data is read. |
| **Find data** | Browse a table by name or topic with click-built filters, counts, groups and links. **Linked tables** also draws the relationship graph around the table and builds a JOIN to any other table from the shortest chain of links. |
| **Find by ID** | Search one value in every table that has a chosen column. |
| **Ask AI** | Plain-language question → SQL to review and run. |
| **QA checks** | Check library with severity (critical → info), tags, owner, expected condition (`empty`, `at most N`, `at least N`, `exactly N`, `info`) and alert rules (`rows > N`, `rows increased`, `query failed`, `slower than Ns`). Run one, all, or only the failing ones; every run's count is recorded (never its rows). |
| **Charts** | Ready-made charts, or chart any saved query. |
| **SQL editor** | SQL with `:named` parameters bound safely, a table helper, a team query library (tags, categories, run counts, “Explain simply”) and history you can filter by database and status. **Analyze** runs `EXPLAIN` (never the query) and explains full table scans, indexes used, rows read, filesort / temporary tables, and patterns such as `DATE(column) = …` or a leading `%` wildcard, with suggestions. |
| **Databases** | Connect your own database (form or `mysql://…` URL), test it before saving, edit or remove it, switch the database everyone's pages use, refresh its table list. Read-only always. |
| **Admin** | Approve accounts, AI key, extra secret / personal column names, activity log. |
| **Privacy policy · Terms of use** | Readable before signing in and linked from the sign-in page and the sidebar. The shipped text describes what this software actually does — set `QA_ORG_NAME` and `QA_CONTACT_EMAIL` in `.env` to name your organisation, or replace it entirely with `local/legal/privacy.md`. |

## Quick start

```bash
git clone https://github.com/jainrakshit24/QA-Data-Desk.git qa_dashboard && cd qa_dashboard
./run.sh
```

New here, or setting this up for a team? **[SETUP.md](SETUP.md)** walks through every step: what to install,
the read-only database user to ask for, the first admin account, `.env`, the AI key, cron and Docker.

Full documentation is in **[docs/](docs/README.md)**: a [user guide](docs/user-guide.md) for every page, the
[configuration reference](docs/configuration.md), [scheduled checks](docs/scheduler.md),
[how it works inside](docs/architecture.md), [security and privacy](docs/security.md), and
[development](docs/development.md).

Open http://localhost:8501. The first run installs the Python packages (no sudo needed) and prints a
**setup code** in the terminal. Enter it on the sign-in page to create the admin account.

Then open **Settings → Databases** and connect a database: label, host, port, database, username, password —
or paste a `mysql://user:password@host:3306/database` URL and let the form fill itself. **Test connection**
tells you straight away whether it works, and nothing is saved until it does. You can connect several
databases and switch between them in the sidebar at any time.

Prefer files? Copy `.env.example` to `.env` (or `.db.json.example` to `.db.json`) before starting.

Ask your DBA for a user with `SELECT` permission only. The app refuses to write anyway — every query is
checked and every session is opened `READ ONLY` — but a read-only user is the safer setup.

## Safety

- **Read-only, twice over.** Every query is checked before it runs (only `SELECT`, `SHOW`, `DESCRIBE`,
  `EXPLAIN`, `WITH`; one statement; no `INSERT`/`UPDATE`/`DELETE`/`DROP`/… anywhere), and every connection
  opens a MySQL `READ ONLY` session, so the database itself refuses a write even if the check were bypassed.
  Still, give the app a database user with `SELECT` permission only.
- **Secrets never shown.** Columns such as `password`, `token`, `secret`, `otp`, `api_key` are hidden for
  everyone, and queries that name them are refused.
- **Personal data masked.** Email addresses and phone numbers are masked for everyone except admins.
- **Sign-in.** Passwords are stored as salted scrypt hashes. New accounts wait for admin approval.
  Five wrong passwords lock an account for 15 minutes. The first admin needs the one-time setup code.
- **Stored results are counts, not rows.** Check history, dashboards and reports record how many rows a check
  returned and how long it took. Schema snapshots store table and column definitions only.
- **Unreachable database.** After a connection times out, further queries to that server fail immediately with a clear
  message for 20 seconds instead of each waiting 15 seconds.
- **Time limit.** Each query stops after 60 seconds. A query with no `LIMIT` of its own is stopped at
  1,000 rows in the SQL editor and Ask AI (changeable there), so `SELECT * FROM a_huge_table` answers in
  about a second instead of timing out. Result tables draw the first 1,000 rows; the CSV download has all of them.
- **Guard against bypasses.** Also refused: executable comments `/*! … */`, `INTO OUTFILE/DUMPFILE/@var`,
  locking reads (`FOR UPDATE`, `FOR SHARE`, `LOCK IN SHARE MODE`), `LOAD_FILE()` and lock functions.
  `tests/test_security.py` holds the bypass attempts that must keep failing.
- **AI sees names, not data.** Gemini receives table names, column names, relationships, your question
  (with emails and phone numbers masked) and SQL text. It never receives database rows, API response values or
  identifier values, and it never runs anything — every AI-written query waits for you to press Run.
- **Evidence is always masked.** Reports mask emails and phone numbers and hide secret fields for every role.
- **No tracking.** Only three strictly necessary cookies (session, XSRF, and a note that you saw the cookie
  message). No analytics, no advertising; Streamlit's own usage statistics are off.
- **Secrets never reach the browser.** Database passwords are write-only in the form, the AI key shows as On/Off,
  and the first-run setup code is printed to the terminal — with a test that asserts none of them is rendered.
- **Named parameters.** `:user_id` in SQL is bound by the driver, never pasted into the query text.

## Your own checks, charts, links and AI notes

The app ships generic and works on any MySQL database. Everything specific to *your* databases goes in
`local/`, which is git-ignored and never published:

| File | What it holds |
|---|---|
| `local/checks.yaml` | Saved QA checks (same format as `config/checks.yaml`). "Add a new check" in the app writes here. |
| `local/charts.yaml` | Ready-made charts. |
| `local/links.yaml` | How tables link when there are no foreign keys, short search words, and topics that pin tables to the top of a search. |
| `local/ai_context.md` | Facts sent to the AI with every question — status codes, which tables are huge, how things join. |
| `local/checks.yaml` fields | `id, title, area, why, sql` plus optional `severity` (critical/high/medium/low/info), `expect` (`empty`, `at most 10`, `at least 1`, `exactly 3`, `info`), `tags`, `owner`, `alerts` (`rows > 0`, `rows increased`, `query failed`, `slower than 10s`), `database`. |
| `local/playbooks.yaml` | Your playbook templates, same format as `config/playbooks.yaml`. Playbooks saved in the app are stored per user and can be shared. |
| `local/consistency_rules.yaml` | Consistency rules (`source`, `target` with `table`, `key`, `filters`; `expect: exists` or `not exists`; optional `sample`). Rules saved in the app are shared by default. |
| `local/validation_rules.yaml` | Response-rule sets for Validate API, e.g. `- name: Login response`, `rules: [{path: status, operator: equals, expected: success}]`. Rule sets saved in the app are stored per user and can be shared. |
| `local/templates/bug_jira.txt` (`bug_markdown.txt`, `bug_text.txt`) | Your team’s bug-report template. Admins can edit it on the Bug evidence page. Placeholders: `{title} {environment} {identifier} {summary} {steps} {expected} {actual} {db_evidence} {api_evidence} {mismatches} {sql} {generated_at}`. |

A check or chart with a `database:` field shows only when the active connection uses that database name.
An item in `local/` with the same `id` as one in `config/` replaces it.

**How links are found:** real foreign keys first, then `local/links.yaml`, then the name pattern
`<name>_id` → a table called `<name>`, `<name>s`, `<name>es` or `<name→ies>` with an `id` column.

## AI helper (optional)

Create a free API key at [Google AI Studio](https://aistudio.google.com), then paste it on
**Admin → AI** (or `GEMINI_API_KEY` in `.env`). The default model is `gemini-flash-lite-latest` (fast), with `gemini-3.5-flash` as a fallback when Google is busy; change it there if Google renames models.
You can also set `GEMINI_API_KEY` and `GEMINI_MODEL` as environment variables.

## Configuration

Everything can be set in a `.env` file next to `app.py` — copy the template and fill in what you need:

```bash
cp .env.example .env
```

`.env` is git-ignored, and real environment variables always win over it (so a container's settings are
never overwritten). Nothing in it is required: you can add databases inside the app instead.

| Setting | How |
|---|---|
| Database connections | **Settings → Databases** in the app, or `.env` / env `QA_DB_HOST` `QA_DB_PORT` `QA_DB_USER` `QA_DB_PASSWORD` `QA_DB_NAME` (+ optional `QA_DB_LABEL`). Connections added in the app are stored in `.db.json` (owner-readable only, git-ignored) |
| App data (accounts, saved queries, activity log) | SQLite file `app_data.sqlite3`, or path in `QA_APP_DB` |
| First admin setup code | Printed on first start, or set your own with `QA_SETUP_CODE` |
| Local pack folder | `local/`, or path in `QA_LOCAL_DIR` |
| Port / listen address | `PORT=8601 ./run.sh`, `ADDRESS=0.0.0.0 ./run.sh` to accept other computers |
| Table list cache | Saved in `cache/`. Compared with the live database every 10 minutes (a 0.1s fingerprint) and refreshed when tables or columns change; also refreshed when a query hits a missing table or column. Refresh by hand on Settings → Databases |
| Alert webhook for cron runs | `QA_ALERT_WEBHOOK`, or `--webhook` on `scheduler.py` |

## Tests

```bash
python3 -m pip install --user -r requirements-dev.txt
python3 -m pytest -q
```

Tests run in random order. Unit tests (read-only guard, privacy masking, filter builder, sign-in,
link detection, config packs, edge cases, join paths, health findings, snapshots) need no database.
Page tests use Streamlit's `AppTest`; the ones that read data run only when a connection is configured.
CI (`.github/workflows/tests.yml`) runs the suite on every push.

Browser tests live in `.project_memory/e2e/` (Playwright with system Chrome) and drive a separate test
server, so they never touch your accounts:

```bash
QA_APP_DB=/tmp/qa_e2e/app.sqlite3 QA_SETUP_CODE=E2ESETUP \
  python3 -m streamlit run app.py --server.port 8599 --server.headless true
E2E_OUT=/tmp/qa_e2e/shots python3 -u .project_memory/e2e/phase3_run.py
```

## Running checks on a schedule

Streamlit is a web app, not a scheduler, so checks run headless from `scheduler.py` and land in the same history
the QA dashboard shows.

```bash
python3 scheduler.py --list                                  # what would run
python3 scheduler.py --schedule daily --notify log           # run today's checks, append logs/checks.log
python3 scheduler.py --tag payments --notify webhook \
        --webhook https://hooks.example.com/… --fail-on alerts
```

Give a check a cadence in YAML (`schedule: daily`), then let cron do the waking up:

```cron
15 8 * * *  cd /home/you/qa_dashboard && /usr/bin/python3 scheduler.py --schedule daily --due-only \
            --notify log,webhook >> logs/scheduler.log 2>&1
```

| Option | What it does |
|---|---|
| `--connection NAME` | Which database (default: the first configured one) |
| `--schedule / --tag / --id / --severity` | Pick the checks to run |
| `--due-only` | Skip checks already run inside their own window — safe to call cron more often than needed |
| `--notify print,log,json,webhook` | Where results go. The webhook stays quiet unless something failed or an alert fired (`--always-notify` overrides) |
| `--fail-on failed\|alerts\|error` | Exit code 1 so cron / CI can act on it |

Notifications carry counts, statuses and check names — never rows. For anything else (email, Jira, Teams), drop a
`local/notify.py` with `send(summary, results)`; it is called after the built-in notifiers and its errors never
break the run.

## Deploying for a team

Before putting this on a network, get approval to expose the database it connects to.

```bash
docker build -t qa-data-desk .
docker run -p 8501:8501 -v qa-data:/data \
  -e QA_DB_HOST=… -e QA_DB_PORT=3306 -e QA_DB_USER=… -e QA_DB_PASSWORD=… -e QA_DB_NAME=… \
  -e QA_SETUP_CODE=choose-a-long-code qa-data-desk
```

- **Put it behind HTTPS.** Streamlit only speaks plain HTTP, so a reverse proxy terminates TLS. `deploy/Caddyfile`
  and `deploy/nginx.conf` ship ready to use: HTTP redirected to HTTPS, HSTS, `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` and `X-Robots-Tag: noindex`, `robots.txt` and
  `sitemap.xml` served at the domain root, and `deploy/404.html` for unknown addresses. The app itself warns
  anyone who reaches it over plain HTTP from another machine.
- **Say who runs it:** `QA_ORG_NAME`, `QA_CONTACT_EMAIL`, `QA_APP_URL` in `.env` — they fill in the privacy policy
  and terms, and `python3 deploy/make_sitemap.py https://your-domain` writes the sitemap.
- The server must be able to reach the database host — staging databases are often IP-restricted or VPN-only.
- Mount `/data` so accounts and saved queries survive restarts.
- Sign-ups need admin approval, so the page can be reachable without everyone getting in.

### Publishing the code

`.gitignore` keeps `.env`, `.db.json`, `.ai.json`, `local/`, `cache/`, `logs/`, the SQLite file, the setup code
and the snapshot key out of git. Before the first push, check that none of them is tracked:

```bash
git ls-files | grep -E "\.env$|\.db\.json$|\.ai\.json$|^local/|^cache/|sqlite3"   # should print nothing
```

## Project layout

```
app.py              sign-in gate, navigation, sidebar
core/               investigation logic without any UI — tested directly
  search.py           global identifier search (batched, capped, indexed-first)
  records.py          record links, JOIN SQL, relationship graph
  timeline.py         events from real timestamp columns only
  api_compare.py      JSON parsing, normalisation, API ↔ DB ↔ expected, response rules
  evidence.py         masked evidence items and report rendering
  params.py           :named parameter binding
  filters.py          click-built filters → parameterised WHERE
  checks.py           check defaults, evaluation, alert rules, change detection
  performance.py      EXPLAIN plan reading and advice
  consistency.py      consistency rule SQL
  table_compare.py    table-to-table field comparison
  playbooks.py        playbook parameters, validation, rendering
  schema_compare.py   structure diff and report
  global_search.py    one search over tables, columns, queries, checks, playbooks, rules, history
  edge_cases.py       edge case ideas per column + a counts-only coverage query
  test_data.py        example rows (never executed) and queries that find existing test data
  schema_graph.py     table neighbourhood graph, shortest join path, JOIN SQL
  health.py           structure-only database health findings
  snapshots.py        masked record snapshots + keyed fingerprints
  env_compare.py      record / query / row-count differences between two connections
  sql_explain.py      plain-English summary of a SELECT, without AI
  errors.py           MySQL errors turned into QA-friendly messages
  legal.py            privacy policy / terms text with the operator's details filled in
store.py            check-run counts, rules, playbooks, snapshots, settings (SQLite)
envfile.py          reads .env into the environment (real env vars always win)
qa_checks.py        loading and running checks for the Checks and Dashboard pages
scheduler.py        headless check runner for cron (no Streamlit)
views/              one file per page
db.py               connections and the read-only guard
privacy.py          secret-column blocking and masking
auth.py             accounts, setup code, saved queries, activity log (SQLite)
schema.py           table/column cache, search, link detection
ai.py               Gemini calls (names only, never data)
packs.py            loads config/ and local/
ui.py               shared result table, charts, errors
config/             generic checks, charts, links, AI notes, report templates, privacy policy and terms (published)
deploy/             Caddy and nginx configs, custom 404, sitemap generator
static/             robots.txt (and the sitemap your domain generates)
local/              your own — not published
docs/               user guide, configuration, scheduler, architecture, security, development
tests/              pytest suite
```

## Troubleshooting

| You see | Do this |
|---|---|
| "Could not reach the database" | Check VPN / network access to the database host, then Admin → Databases → Test. |
| "The query ran longer than 60 seconds" | Filter on a column marked 🔑 (indexed), narrow the date range, or add `LIMIT`. |
| A table is missing from search | Admin → Databases → Refresh table list. |
| "Compare environments" says you need a second connection | Add one on Admin → Databases. To try the page out, add the same database twice under different names: everything should then report no differences. |
| An exact row count takes forever | On Compare environments → Row counts, switch to "Fast estimate (table statistics)". |
| A check shows 🟠 Outdated | The table or column it uses was removed from the database. Fix its SQL in `local/checks.yaml` or delete it. |
| Changed code, nothing happened | The server does not reload code while running. Stop it and start `./run.sh` again. |
| "Gemini rejected the API key" / "model not found" | Update the key or model name on Admin → AI. |
| Locked out as the only admin | Stop the app and run `python3 -c "import auth; auth.set_status(1, 'active')"`, or delete `app_data.sqlite3` to start over (this removes all accounts and saved queries). |

---

© 2026 **Rakshit Jain** · Built by Rakshit Jain ([@jainrakshit24](https://github.com/jainrakshit24)) · MIT licensed — see [LICENSE](LICENSE).
