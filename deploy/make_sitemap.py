#!/usr/bin/env python3
# QA Data Desk — https://github.com/jainrakshit24/QA-Data-Desk
# Copyright (c) 2026 Rakshit Jain. Licensed under the MIT License.

"""Write static/sitemap.xml and static/robots.txt for this installation.

    python3 deploy/make_sitemap.py https://qa.example.com

Only the pages a signed-out visitor can reach are listed: the sign-in page and the two legal documents.
Everything else needs an account, so listing it would be noise. robots.txt disallows everything either way —
an internal tool has no business in a search index.
"""
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_PAGES = [("sign-in", "1.0", "monthly"), ("privacy", "0.5", "yearly"), ("terms", "0.5", "yearly")]


def sitemap(base_url, today=None):
    base = base_url.rstrip("/")
    today = today or dt.date.today().isoformat()
    urls = "\n".join(
        f"  <url>\n    <loc>{base}/{path}</loc>\n    <lastmod>{today}</lastmod>\n"
        f"    <changefreq>{freq}</changefreq>\n    <priority>{prio}</priority>\n  </url>"
        for path, prio, freq in PUBLIC_PAGES)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'{urls}\n</urlset>\n')


def robots(base_url):
    base = base_url.rstrip("/")
    return ("# QA Data Desk — an internal, sign-in-only tool. Nothing here should be indexed.\n"
            "User-agent: *\nDisallow: /\n\n"
            f"Sitemap: {base}/sitemap.xml\n")


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    base = argv[1]
    if not base.startswith("https://"):
        print("Use the full https:// address of this installation.", file=sys.stderr)
        return 2
    out = os.path.join(HERE, "static")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "sitemap.xml"), "w") as f:
        f.write(sitemap(base))
    with open(os.path.join(out, "robots.txt"), "w") as f:
        f.write(robots(base))
    print(f"Wrote static/sitemap.xml and static/robots.txt for {base}")
    print("Serve them at the domain root — see deploy/Caddyfile or deploy/nginx.conf.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
