# Notes for the AI query writer

Sent to Gemini with every question, together with table and column names. Never any data rows.
Write facts that are true for every database here. Put notes about your own databases
(status codes, how tables join, which tables are huge) in local/ai_context.md — that file is not published.

- The database is MySQL 8. Write MySQL syntax.
- When a table has no foreign keys, columns usually link by name: `<name>_id` points to `<name>.id` or `<name>s.id`.
- Small integer `status` or `type` columns mean different things in different tables. Do not guess their meaning;
  say it is an assumption.
- Prefer filtering on indexed columns for tables with millions of rows.
