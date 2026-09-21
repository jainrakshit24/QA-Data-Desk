# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.
# Author: Rakshit Jain <itsrakshitjain@gmail.com>

"""Privacy policy, terms of use and the cookie notice.

The text ships in `config/legal/` so every installation has something honest to show, and can be replaced per
organisation by putting a file with the same name in `local/legal/`. Placeholders are filled from the environment,
so nobody has to edit markdown to publish it under their own name.
"""
import datetime as dt
import os

import packs

DOCUMENTS = {"privacy": "Privacy policy", "terms": "Terms of use"}

# Strictly necessary only — there is no analytics or advertising cookie in this app.
COOKIES = [
    ("Session cookie", "Keeps you signed in while you use the tool", "Strictly necessary"),
    ("XSRF token", "Stops another site from submitting actions as you", "Strictly necessary"),
    ("Cookie-notice acknowledgement", "Remembers that you have seen this notice", "Strictly necessary"),
]


def settings():
    """Who operates this installation. Set these in .env so the documents name the right organisation."""
    return {
        "org": os.environ.get("QA_ORG_NAME", "").strip() or "The team running this installation",
        "app": os.environ.get("QA_APP_NAME", "").strip() or "QA Data Desk",
        "contact": os.environ.get("QA_CONTACT_EMAIL", "").strip() or "your administrator",
        "url": os.environ.get("QA_APP_URL", "").strip(),
        "updated": os.environ.get("QA_LEGAL_UPDATED", "").strip() or dt.date.today().strftime("%d %B %Y"),
    }


def _path(name):
    local = os.path.join(packs.LOCAL_DIR, "legal", f"{name}.md")
    if os.path.exists(local):
        return local
    return os.path.join(packs.CONFIG_DIR, "legal", f"{name}.md")


def document(name):
    """The markdown for 'privacy' or 'terms', with {placeholders} filled in."""
    if name not in DOCUMENTS:
        raise KeyError(f"Unknown legal document: {name}")
    path = _path(name)
    if not os.path.exists(path):
        return f"# {DOCUMENTS[name]}\n\nThis installation has not published a {DOCUMENTS[name].lower()} yet."
    with open(path) as f:
        text = f.read()
    values = settings()
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def is_customised(name):
    """True when this installation replaced the shipped text with its own."""
    return _path(name).startswith(os.path.join(packs.LOCAL_DIR, ""))


def cookie_notice():
    return ("This tool uses only strictly necessary cookies — a session cookie to keep you signed in, an XSRF "
            "token, and a note that you have seen this message. No analytics, no advertising, no tracking.")
