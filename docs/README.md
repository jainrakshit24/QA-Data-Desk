# QA Data Desk — documentation

A read-only workspace for QA engineers working against a MySQL database: start from a bug or an identifier,
trace the record through related tables, validate what an API returns, prove what you found, and keep the
checks running.

| Guide | Read it when |
|---|---|
| **[Setup](../SETUP.md)** | You are installing it for the first time, or deploying it for a team |
| **[User guide](user-guide.md)** | You want to know what each page does and how to use it |
| **[Configuration](configuration.md)** | You are adding your own checks, charts, links, playbooks or templates |
| **[Scheduled checks](scheduler.md)** | You want checks to run from cron and tell you when something breaks |
| **[Architecture](architecture.md)** | You want to know how it works inside, or you are changing the code |
| **[Security and privacy](security.md)** | You need to explain what it can and cannot do to your data |
| **[Development](development.md)** | You are adding a feature, a page or a test |

## The short version

1. `./run.sh`, create the admin account with the printed setup code, connect a database on **Settings → Databases**.
2. Paste an id, an email or a reference on **Start investigation** to find where it lives.
3. Open the record, follow its links, build a timeline.
4. Compare an API response with the row on **Validate API**.
5. Collect what matters with **Add to evidence**, export a Jira-ready report on **Bug evidence**.
6. Turn anything repeatable into a **QA check** or a **playbook**, and let `scheduler.py` run it from cron.

## What it never does

- It never writes to a connected database — not with a feature, not through AI, not by accident.
- It never shows a secret column, and masks emails and phone numbers for non-admins.
- It never sends your data to an AI. Only table names, column names and SQL text can go out, and only if you
  configure a key.
- It never stores database rows in its own files. Only counts, definitions and masked snapshots you asked for.

The rest of this documentation explains how each of those is enforced.

---

© 2026 Rakshit Jain · MIT licensed.
