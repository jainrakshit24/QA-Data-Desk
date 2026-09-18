# User guide

Every page, what it is for, and how to drive it. Pages are grouped in the sidebar exactly as they are here.

- [Signing in](#signing-in)
- [Investigate](#investigate) — Start investigation · Investigate record · Playbooks · Bug evidence
- [Validate](#validate) — Validate API · Data consistency · Schema compare · Compare environments · Test ideas
- [Monitor](#monitor) — QA dashboard · QA checks · Database health
- [Explore](#explore) — Search everything · Find data · Find by ID · Ask AI · Charts · SQL editor
- [Settings](#settings) — My account · Databases · Admin
- [Things that apply everywhere](#things-that-apply-everywhere)

---

## Signing in

The first account is the admin and needs the one-time **setup code** printed by `run.sh` (or set as
`QA_SETUP_CODE`). Everyone else signs up without a code and an admin approves them on **Admin → People**.
Five wrong passwords lock an account for 15 minutes. Accounts live in a local SQLite file, never in your database.

---

## Investigate

### Start investigation
The home page, and the right place to begin when someone hands you a bug.

**Two ways in:**

1. **An identifier** — paste a value: an id, an email, a mobile number, an order reference. The app searches every
   indexed column in the database whose name looks like an identifier, in batches, and shows which tables and
   columns contain it with a row count each. Click a hit to open that record.
   - Numbers skip every table's plain `id` primary key by default, because `10908` means something different in
     each table. Tick the option if you really want those.
   - Only indexed columns are searched unless you ask for more — this keeps a 1,300-table database responsive.
2. **A bug description** — type what was reported ("campaign resumed but not visible in listing"). With an AI key
   configured, you get an investigation plan: what the tool understood, the facts you actually stated, possible
   explanations clearly marked as unconfirmed, the data paths to follow, and SQL steps. You review each step and
   press Run. The AI never runs anything and never sees your data — only table and column names.

The plan can be saved as a **playbook** so the next person does not start from scratch.

### Investigate record
One record, fully unpacked. Choose a table, a key column and a value (or arrive here by clicking a search hit).

- **Fields** — the row itself, masked. Secret columns show as hidden; emails and phone numbers are masked unless
  you are an admin.
- **Linked records** — every table that points at this record or that it points to, with a row count. Links come
  from real foreign keys, from `local/links.yaml`, and from the `<name>_id → <name>.id` naming pattern. Open any
  linked record and a breadcrumb lets you walk back.
- **Graph** — the same relationships drawn, with counts on the edges.
- **Timeline** — events built only from timestamp columns that actually hold a value in these rows. Nothing is
  inferred; linked tables contribute up to 20 rows each.
- **All matching rows** — when the value is not unique.
- **Snapshots** — save what the record looks like now, then compare later against the live record or another
  snapshot. Values are stored masked, plus a keyed fingerprint, so a change in a masked field is still detected
  without the real value ever being stored. These are manual snapshots, not database history: only the moments
  someone pressed Save can be compared.

### Playbooks
A playbook is a saved investigation: a name, an objective, parameters, and ordered SQL steps with a purpose and a
"look for" note. Run one step at a time or all at once.

- Parameters: `:value` is a **value** and is always bound safely; `{{table}}` and `{{column}}` are **names** and are
  checked against the live schema before the query is built, so a playbook cannot be turned into an injection.
- Ships with generic templates: duplicates in a column, orphan rows, missing values, status breakdown, record
  neighbours.
- Every step shows a plain-English summary ("In short: returns 2 columns from campaigns joined with leads…") and,
  with AI configured, an **Explain simply** button.
- Duplicate a template to make it yours; share it with the team; export results to evidence in one click.

### Bug evidence
The basket. Anything with an **Add to evidence** button lands here: a record, a query result, an API comparison, a
schema difference, a health report.

- Choose a template — Jira, Markdown or plain text — fill in summary, steps, expected and actual, and the report is
  assembled with your evidence attached.
- **Everything is masked, including for admins.** A bug report leaves the building; the tool assumes that.
- Templates live in `config/templates/` and can be overridden per team in `local/templates/`.

---

## Validate

### Validate API
Two tabs.

**API ↔ Database** — paste a JSON response, pick the table and the row it should match, and map fields. Mapping is
automatic by name where it can be. Each row is reported as Match, Mismatch, Missing in API or Missing in DB. Add an
**Expected** column and it becomes a three-way check that tells you which layer is wrong: the API, the database, or
the expectation itself.

Normalisation is configurable, because "different" is often just formatting: ignore letter case, ignore surrounding
spaces, treat `"5"` and `5` as equal, `true` and `1` as equal, compare dates as instants, treat empty text as NULL.

**Response rules** — check a response on its own without a database: equals, exists, not null, count, regex, type,
status code. Save rule sets and reuse them per endpoint.

### Data consistency
**Rules** — express an invariant in words: *every row in `payments` where `status = SUCCESS` must have a row in
`leads` with the same `user_id`*. The page shows how many rows were checked, how many passed and the failing rows
themselves. Run it over all rows or only the newest N when the table is large. Save the rule for the team, or
export it as a QA check so it runs on a schedule.

**Compare tables** — map fields between two tables joined on a key and see matching, mismatched, missing on either
side, and duplicates — with the same normalisation options as the API validator.

### Schema compare
Compare two connections, or one connection against a saved snapshot of its own structure. You get missing and extra
tables, missing and extra columns, and changes in type, nullability, index or default — plus the tables whose row
counts moved a lot. Take a snapshot before a deploy or a data refresh and compare afterwards. Download as TXT or CSV.

### Compare environments
Same question, two databases. Three tabs:

- **One record** — the same row in both, field by field, masked, with only the differences by default.
- **One query** — one read-only query run on both, matched on a key column: rows only in A, rows only in B, and the
  fields that differ.
- **Row counts** — exact `COUNT(*)` on both, or a fast estimate from table statistics when the tables are huge.
  Tables missing on one side are reported as missing, not as zero.

Structure differences belong on Schema compare; this page is about data.

### Test ideas
Two tabs, both suggestion-only.

**Edge cases** — for every column, derived from its type, name, key and links:
empty and NULL, length limits (exactly N and N+1 characters), number ranges including the type's real maximum,
dates (leap day, zero date, month and year ends, the timezone boundary, the 2038 `TIMESTAMP` cliff), every enum
value plus one that is not in the list, duplicates on unique columns, orphan links, and cross-column cases such as
*end before start* or *active status on a soft-deleted row*. Filter by category or column, download as CSV, or push
into evidence.

Then **Count in the database** answers "do rows like this already exist?" with one query that returns **counts only**
— NULLs, empty strings, values with stray spaces, the longest text, zeros and negatives, and date ranges — over the
newest 5,000 rows. Personal columns are counted but never shown.

**Test data** — example rows in four shapes (typical, everything at its limit, empty where allowed, unicode and
quotes), as `INSERT` SQL or JSON. The SQL is clearly labelled `-- NOT EXECUTED`, and this app cannot run it: the
read-only guard blocks writes. Link columns become `:existing_<table>_<column>` placeholders instead of invented ids.
Below that, **Find existing data instead** gives read-only queries that locate rows already in the state you need
(newest rows, one example per status, soft-deleted rows, empty fields, longest values, orphan links). With AI
configured you can also describe a scenario and get suggestions — any write SQL it returns is dropped.

---

## Monitor

### QA dashboard
The state of every check: run today, passed, failed, warnings, total issues, the change since the previous run, a
trend per day, and which checks changed the most. Export a data quality report as TXT, CSV or XLSX.

Only counts are stored per run — never the rows — so history is safe to keep and safe to share.

### QA checks
The check library. Each check has severity (critical → info), tags, an owner, an expectation (`empty`,
`at most N`, `at least N`, `exactly N`, `info`) and optional alert rules (`rows > N`, `rows increased`,
`query failed`, `slower than N s`).

Run one, run all, or re-run only what failed. A check whose SQL uses a table or column that no longer exists is
marked **outdated** rather than silently failing. Admins can add a check from the page; it is written to
`local/checks.yaml`.

### Database health
Structure-only findings, computed from the cached schema, so it is instant and reads no data:

- tables with no primary key, or no index at all
- link columns whose type differs from their target (`int` vs `bigint`, signed vs unsigned) — a silent join killer
- link columns on large tables that are not indexed
- tables where almost every column is nullable, very wide tables, empty tables
- tables whose name looks like a leftover copy (`_bak`, `_old`, `_tmp`, a date suffix)

Select a row to open that table in Find data. Download as CSV or add to evidence.

---

## Explore

### Search everything
One box over everything the app knows: table names, column names, your saved and shared queries, QA checks,
playbooks, consistency rules and your query history. Multi-word search requires every word to match. Results are
grouped and ranked (exact name first), and **Open** takes you to the right page with the right thing already
selected. There is also a search box in the sidebar from any page.

### Find data
Browse a table without SQL. Search by table name or topic (partial words work: "user log" finds `users_log` and
`user_login_log`), then:

- **Rows & filters** — click-built conditions (equals, contains, starts with, between, one of, is empty…). Values
  are always bound as parameters, never pasted into SQL.
- **Count & group** — counts by one or two columns, with a chart.
- **Columns** — types, keys, defaults, comments.
- **Linked tables** — what this table points to and what points at it, plus the **relationship graph** around it
  (one or two links away) and a **JOIN builder**: pick any other table and the shortest chain of links becomes
  ready-to-run SQL with `:id` as the parameter.

Columns marked 🔑 are indexed — filter on those when a table is large.

### Find by ID
One value, every table that has a chosen column. Useful when you know the column ("`campaign_id`") but not where it
is used. All the counting queries run in parallel.

### Ask AI
Ask in plain language, get SQL you can review and run. It sees the table and column names of the tables it thinks
are relevant, plus your notes from `config/ai_context.md` and `local/ai_context.md` — never data. If the query
fails, the error can be sent back for a fix (again, names only).

### Charts
Ready-made charts from `config/charts.yaml` and `local/charts.yaml`, or chart any saved query. Results are cached
for ten minutes so switching charts is instant.

### SQL editor
For when you do want to write SQL.

- `:named` parameters are bound safely; a helper panel lists tables and columns.
- **Analyze** runs `EXPLAIN` — never the query — and explains full table scans, which index was used, how many rows
  are read, filesort and temporary tables, and patterns such as `DATE(column) = …` or a leading `%` wildcard that
  stop an index from being used.
- **Query library** — your queries and the team's, with tags, categories, run counts and last-run time; Load, Run,
  Explain simply, Edit, Delete.
- **History** — everything you ran, filterable by database and status, with Load, Run again and Save. The SQL is
  stored with sensitive values masked out.

A query with no `LIMIT` gets one added automatically, so a stray `SELECT *` on a huge table returns in a second
instead of timing out. Anything that is not a read is refused before it reaches the database.

---

## Settings

### My account
Change your name and password. See what you have saved and run.

### Databases
Connect your own database and switch between them.

- The table lists every connection: host, port, database, username, whether it came from `.env` or was added in the
  app, and which one is currently in use. Passwords are never shown.
- **Add a database** — fill the form, or paste `mysql://user:password@host:3306/database` and let it fill itself.
  **Test connection** runs first; nothing is saved unless it succeeds.
- **Change or remove** — edit any detail (leave the password blank to keep the saved one), or remove the connection.
  Removing it does not touch the database, your saved queries or check history.
- **Switch** changes the database every page uses. **Refresh table list** re-reads the structure immediately.
- Admins add and edit; everyone can see the list and switch. Connections that come from `.env` are read-only here.

### Admin
- **People** — approve, promote, disable accounts.
- **Databases** — a summary that links to the Databases page.
- **AI** — paste a Gemini key, choose the model, test it, or remove the key.
- **Privacy** — add your own secret column names (hidden from everyone) and personal column names (masked for
  non-admins). They apply immediately, everywhere, including logs.
- **Activity** — who ran what, when, against which database, how long it took, and whether it failed. SQL is stored
  masked.

---

## Things that apply everywhere

**Add to evidence** appears next to most results — that is how a report gets built.

**Every result table** can be downloaded as CSV, and shows how long the query took and whether it was truncated.

**Errors are written for QA, not for DBAs.** An unknown column, a missing table, a syntax error, a timeout, a
connection failure or an ambiguous column each come back with what happened, why it usually happens, and what to
try next. The raw MySQL error is one click away, with sensitive values masked.

**Limits you cannot exceed:** 60 seconds per statement, 20,000 rows per query into the browser. Both are there so
one person's mistake cannot slow the database down for everyone.

**Nothing you do here can change data.** Every statement is checked before it runs and then runs inside a read-only
transaction. See [Security and privacy](security.md).

---

© 2026 Rakshit Jain · MIT licensed.
