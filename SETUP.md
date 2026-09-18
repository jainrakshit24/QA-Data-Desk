# Setup guide

Everything needed to get QA Data Desk running — on your own laptop, or on a server for your team.
No prior Streamlit knowledge assumed. If you only want the feature tour, read the [README](README.md) instead.

- [1. What you need first](#1-what-you-need-first)
- [2. Install and start](#2-install-and-start)
- [3. Create the admin account](#3-create-the-admin-account)
- [4. Connect a database](#4-connect-a-database)
- [5. Settings in .env](#5-settings-in-env)
- [6. Turn on the AI helper (optional)](#6-turn-on-the-ai-helper-optional)
- [7. Add your own checks, charts and links](#7-add-your-own-checks-charts-and-links)
- [8. Run checks on a schedule (optional)](#8-run-checks-on-a-schedule-optional)
- [9. Running it for a team](#9-running-it-for-a-team)
- [10. Where your data is kept](#10-where-your-data-is-kept)
- [11. Updating](#11-updating)
- [12. Troubleshooting](#12-troubleshooting)

---

## 1. What you need first

| You need | Why | How to check |
|---|---|---|
| **Python 3.10 or newer** | The app is Python. 3.12 is what CI uses. | `python3 --version` |
| **pip** | Installs the packages. | `python3 -m pip --version` |
| **A MySQL 5.7+ / MariaDB database** | The thing you want to investigate — usually staging. | See step 4 |
| **Network access to that database** | Staging databases are often VPN-only or IP-restricted. | `nc -vz your-db-host 3306` |

You do **not** need the MySQL command-line client, Docker, or admin rights on your machine.

### Ask for a read-only database user

The app refuses to write — every query is checked, and every session is opened `READ ONLY` — but the safest setup
is a database user that cannot write either. Send your DBA this:

```sql
CREATE USER 'qa_readonly'@'%' IDENTIFIED BY 'a-long-random-password';
GRANT SELECT ON your_database.* TO 'qa_readonly'@'%';
-- Optional, and only if you want the Analyze button on the SQL editor to explain query plans:
GRANT SHOW VIEW ON your_database.* TO 'qa_readonly'@'%';
FLUSH PRIVILEGES;
```

`SELECT` on `information_schema` comes for free and is what the table list is built from.

---

## 2. Install and start

```bash
git clone https://github.com/<your-account>/<your-repo>.git qa_dashboard
cd qa_dashboard
./run.sh
```

`run.sh` installs the Python packages on first run (into your user folder — no `sudo`), then starts the app and
prints a URL. Open **http://localhost:8501**.

Prefer to do it by hand, or want a virtual environment?

```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m streamlit run app.py
```

Useful variations:

```bash
PORT=8601 ./run.sh                 # a different port
ADDRESS=0.0.0.0 ./run.sh           # let other computers on the network reach it
```

---

## 3. Create the admin account

The first account is the admin, so it is protected by a one-time **setup code**:

1. `run.sh` prints the code in the terminal on first start:
   `First run — admin setup code: XXXXXXXX`
   (You can choose your own instead: put `QA_SETUP_CODE=something-long` in `.env` — see step 5.)
2. In the browser, open the **Create account** tab.
3. Fill in name, email, username, a password of at least 10 characters with a letter and a number, and paste the
   setup code.
4. That account becomes the admin.

**Everyone else** signs up from the same page **without** a code, and an admin approves them on
**Settings → Admin → People**. Accounts are stored in a local SQLite file, not in your database.

Forgot the code? It is in the `.setup_code` file next to the app (delete that file and restart to get a new one,
as long as no account exists yet).

---

## 4. Connect a database

Open **Settings → Databases** and click **Add a database**.

| Field | Example | Notes |
|---|---|---|
| Label | `Staging · shop` | What you will see in the sidebar |
| Host | `db.staging.example.com` | Hostname or IP |
| Port | `3306` | MySQL default |
| Database | `shop` | The schema to open |
| Username / Password | `qa_readonly` / … | Read-only user from step 1 |

Two shortcuts:

- **Paste a connection URL instead** — `mysql://user:password@host:3306/database` fills the form for you.
- **Test connection** — tells you straight away whether it works. Nothing is saved until a test succeeds.

You can add several databases and switch between them from the sidebar at any time. Connections added here are
stored in `.db.json` next to the app, readable only by the user that runs it, and git-ignored. A connection that
comes from `.env` is marked *environment* and is changed there, not in the app.

**First query slow?** The app reads the table list once and caches it in `cache/`. On a database with thousands of
tables this takes a few seconds; after that it is instant and refreshes itself when the structure changes.

---

## 5. Settings in .env

Anything you would rather not type in the app goes into a `.env` file next to `app.py`:

```bash
cp .env.example .env
```

`.env` is git-ignored, and real environment variables always win over it (so a container's settings are never
overwritten by a stray file). Nothing in it is required.

| Variable | What it does |
|---|---|
| `QA_DB_HOST` `QA_DB_PORT` `QA_DB_USER` `QA_DB_PASSWORD` `QA_DB_NAME` | One database connection, provided by the environment. Handy for Docker or a shared server. |
| `QA_DB_LABEL` | How that connection is named in the sidebar |
| `QA_SETUP_CODE` | Your own setup code for the first admin |
| `QA_APP_DB` | Where accounts, saved queries, check history and snapshots live (default `app_data.sqlite3` next to the app) |
| `QA_LOCAL_DIR` | Folder with your team's own checks, charts, links and AI notes (default `local/`) |
| `GEMINI_API_KEY` `GEMINI_MODEL` | AI helper (step 6) |
| `QA_ALERT_WEBHOOK` | Where scheduled check alerts are posted (step 8) |

---

## 6. Turn on the AI helper (optional)

Everything works without AI. With a key, you also get: table suggestions, plain-language → SQL, "explain this
query simply", investigation plans from a bug description, and test-scenario ideas.

1. Create a free key at [Google AI Studio](https://aistudio.google.com/apikey).
2. Paste it on **Settings → Admin → AI** (or set `GEMINI_API_KEY` in `.env`), then press **Test AI**.

What is sent to Google: table names, column names, your question and SQL text. **Never** rows from your database,
and never the identifier values you search for. The AI never runs anything by itself — you review the SQL and press
Run. On Google's free tier, prompts may be used to improve Google's models, so keep company-specific notes out of
`config/ai_context.md` if that matters to you (use `local/ai_context.md`, which is git-ignored).

---

## 7. Add your own checks, charts and links

The repo ships a few generic checks and charts in `config/`. Anything specific to your company belongs in
`local/`, which is git-ignored and never published:

| File | What it holds |
|---|---|
| `local/checks.yaml` | Your QA checks — the queries that should return nothing when all is well |
| `local/charts.yaml` | Ready-made charts for the Charts page |
| `local/links.yaml` | Relationships the app cannot guess (no foreign key, no `<name>_id` pattern) |
| `local/ai_context.md` | Domain notes sent with every AI question ("a campaign is…") |
| `local/playbooks.yaml`, `local/validation_rules.yaml`, `local/templates/` | Saved investigations, API rules, bug report templates |

A check is just a query with an expectation:

```yaml
- id: paid_without_lead
  area: Payments
  title: Successful payment without a lead
  why: Every successful payment should create a lead. Rows here are missing leads.
  severity: high
  expect: empty            # empty | info | at most N | at least N | exactly N
  schedule: daily          # optional, used by scheduler.py
  tags: [payments, leads]
  alerts: ["rows > 0", "rows increased"]
  database: shop           # optional: only show for this database
  sql: |
    SELECT p.id, p.created_at
    FROM payments p
    LEFT JOIN leads l ON l.payment_id = p.id
    WHERE p.status = 'SUCCESS' AND l.id IS NULL
    LIMIT 200
```

Admins can also add checks from the app (**Monitor → QA checks → Add a new check**); they are appended to
`local/checks.yaml`.

---

## 8. Run checks on a schedule (optional)

Streamlit is a web app, not a scheduler, so checks run from the command line and land in the same history the QA
dashboard shows.

```bash
python3 scheduler.py --list                        # what would run
python3 scheduler.py --schedule daily --notify log # run and append logs/checks.log
```

Then let cron wake it up:

```cron
15 8 * * *  cd /home/you/qa_dashboard && /usr/bin/python3 scheduler.py --schedule daily --due-only \
            --notify log,webhook >> logs/scheduler.log 2>&1
```

- `--due-only` skips checks that already ran inside their own window, so calling cron more often is harmless.
- `--notify webhook` posts to `QA_ALERT_WEBHOOK` (Slack-compatible JSON) and stays silent unless something failed
  or an alert fired.
- `--fail-on failed|alerts|error` exits with code 1 so cron or CI can act on it.
- For anything else — email, Jira, Teams — drop a `local/notify.py` with `send(summary, results)`.

Notifications carry check names, statuses and counts. Never rows, never values.

---

## 9. Running it for a team

Before putting this on a network, get approval to expose the database it connects to.

```bash
docker build -t qa-data-desk .
docker run -p 8501:8501 -v qa-data:/data \
  -e QA_DB_HOST=… -e QA_DB_PORT=3306 -e QA_DB_USER=… -e QA_DB_PASSWORD=… -e QA_DB_NAME=… \
  -e QA_SETUP_CODE=choose-a-long-code qa-data-desk
```

- Put it behind HTTPS (a reverse proxy such as Caddy or nginx). Streamlit alone serves plain HTTP.
- The server must be able to reach the database host — staging databases are often VPN-only.
- Mount `/data` so accounts, saved queries and check history survive restarts.
- Sign-ups need admin approval, so the page can be open without everyone getting access.
- Five wrong passwords lock an account for 15 minutes.

---

## 10. Where your data is kept

| Path | What | Published to git? |
|---|---|---|
| `app_data.sqlite3` | Accounts, saved queries, activity log, check history, rules, playbooks, snapshots | No |
| `.db.json` | Database connections added in the app (passwords in plain text, file mode 600) | No |
| `.env` | Your environment settings | No |
| `.ai.json` | Gemini key and model, if saved from the app | No |
| `.setup_code` / `.snapshot_key` | First-admin code; the key that fingerprints record snapshots | No |
| `cache/` | Table and column lists per connection | No |
| `local/` | Your own checks, charts, links, notes, templates | No |
| `logs/` | Scheduler output | No |
| `config/`, `core/`, `views/`, `tests/` | The app itself | Yes |

**No rows from your database are ever stored.** Check history keeps counts; record snapshots keep masked values plus
a keyed fingerprint; the activity log keeps the SQL text with sensitive values masked out.

---

## 11. Updating

```bash
cd qa_dashboard
git pull
python3 -m pip install -r requirements.txt   # only if requirements changed
./run.sh
```

Your accounts, connections, `local/` pack and history are untouched by a pull — they all live in git-ignored files.
The SQLite file migrates itself on start when a new version adds columns.

To verify a new version before letting the team in:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q            # database-backed tests skip themselves if no connection is configured
```

---

## 12. Troubleshooting

| You see | What it means | Do this |
|---|---|---|
| `Could not reach the database` | The host is not reachable from this machine | Check VPN / IP allow-list, then **Settings → Databases → Test connection** |
| `Access denied for user …` | Wrong username or password, or the user is not allowed from this host | Ask your DBA to confirm the grant includes your IP (`'user'@'%'`) |
| `The query ran longer than 60 seconds` | The 60-second limit stopped it | Filter on a column marked 🔑 (indexed), narrow the date range, or use **Analyze** on the SQL editor to see the plan |
| `Only read queries can run here` | You typed something that writes | That is the point — the app is read-only |
| A table is missing | The structure changed after the list was cached | **Settings → Databases → Refresh table list** |
| Port 8501 already in use | Something else is running there | `PORT=8601 ./run.sh` |
| The page keeps its old behaviour after you edit code | The file watcher is off on purpose | Stop the app and start it again |
| `ModuleNotFoundError` on start | Packages missing for this Python | `python3 -m pip install -r requirements.txt` |
| Nothing is printed and the browser shows nothing | The app crashed on start | Look at the terminal output — the last line names the file and problem |

Still stuck? Open an issue with what you did, what you expected, and the message you saw. Never paste real
credentials or database rows into an issue.
