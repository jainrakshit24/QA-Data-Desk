# Privacy policy

_Last updated: {updated}_

{org} runs **{app}**, an internal, read-only tool for inspecting databases that {org} already controls.
This policy explains what the tool stores about **you as a user of the tool**. It is not a policy about the
data inside the databases you connect — that data stays in those databases and is governed by {org}'s own
policies.

## Who is responsible

{org} operates this installation. Questions, access requests or deletion requests: **{contact}**.

## What we store about you

| Data | Why | Where it is kept | How long |
|---|---|---|---|
| Your name, username, email address | To create your account and show who did what | The tool's own database on the server | Until your account is deleted |
| Password (salted scrypt hash, never the password itself) | To sign you in | Same | Same |
| Sign-in times, failed attempts, lock state | To stop password guessing | Same | Same |
| Queries you run: the SQL text with sensitive values removed, row counts, durations, errors, which database | So the team can see activity and debug problems | Same | Until an admin clears it |
| Things you save: queries, playbooks, rules, snapshots, check results | Because you asked the tool to save them | Same | Until you or an admin delete them |

## What we deliberately do not store

- **No rows from your databases.** Check results are stored as counts. Record snapshots store masked values plus
  a one-way keyed fingerprint, never the original value.
- **No secret columns, ever.** Passwords, tokens, API keys, OTPs and similar columns are refused at query time.
- **No tracking, advertising or analytics.** There is no third-party analytics script, no advertising pixel and
  no behavioural profiling. Streamlit's own usage statistics are switched off in this installation.

## Masking

Email addresses and phone numbers are masked for anyone who is not an admin, and masked **for everyone** in
exported reports, downloads and the activity log. Admins can add more column names to that list.

## Cookies

The tool sets only what it needs to work:

| Cookie | Purpose | Type |
|---|---|---|
| Session cookie | Keeps you signed in while you use the tool | Strictly necessary |
| XSRF token | Prevents another site from submitting actions as you | Strictly necessary |
| Cookie-notice acknowledgement | Remembers that you saw the notice | Strictly necessary |

There are no analytics or advertising cookies, so there is nothing to opt out of. Blocking the strictly necessary
cookies will stop sign-in from working.

## Artificial intelligence

The AI helper is optional and off unless an administrator configures a key. When it is on, the tool may send
**table names, column names, your typed question and SQL text** to Google's Gemini API. It never sends rows,
values from your database, or the identifier you searched for, and the AI cannot run anything by itself.
If your organisation uses a free Google tier, Google may use those prompts to improve its models.

## Who can see your data

- Other signed-in users of this installation can see shared queries, playbooks and rules, and the activity log if
  they are admins.
- Administrators of the server can read the files on it.
- Nothing is sent to the author of the software. There is no telemetry.

## Your choices

Ask **{contact}** to see what is stored about you, correct it, or delete your account and the records attached to
it. Account deletion removes your profile and your private saved items; shared items can be transferred or removed
on request.

## Changes

Material changes to this policy will be announced inside the tool. The date at the top always reflects the current
version.
