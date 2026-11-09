#!/usr/bin/env python3
"""Find open PRs with human comments (reviews, review comments, conversation) that the author has not
answered since. Non-bot, non-self authors only.

Usage: python human_scan.py [--repos owner/a,owner/b] [--author alosslessdev]
"""
import json
import re
import subprocess
import sys

import os
AUTHOR = os.environ.get("PR_AUTHOR", "alosslessdev")
BOTS = re.compile(r"coderabbit|\[bot\]|github-actions|dependabot|sonar|codecov", re.I)


def gh(*a):
    p = subprocess.run(["gh", *a], capture_output=True, text=True, encoding="utf-8")
    return p.stdout if p.returncode == 0 else ""


def ghj(*a):
    try:
        return json.loads(gh(*a) or "{}")
    except Exception:
        return {}


def paginated(url):
    out = gh("api", "--paginate", url)
    if not out:
        return []
    dec, res, i = json.JSONDecoder(), [], 0
    while i < len(out):
        while i < len(out) and out[i] in " \r\n\t":
            i += 1
        if i >= len(out):
            break
        obj, i = dec.raw_decode(out, i)
        res.extend(obj if isinstance(obj, list) else [obj])
    return res


def scan(repo, author):
    prs = [p for p in paginated(f"repos/{repo}/pulls?state=open&per_page=100")
           if (p.get("user") or {}).get("login") == author]
    out = []
    for pr in prs:
        n = pr["number"]
        events = []
        for r in paginated(f"repos/{repo}/pulls/{n}/reviews?per_page=100"):
            login = (r.get("user") or {}).get("login") or ""
            if BOTS.search(login) or login == author:
                continue
            body = (r.get("body") or "").strip()
            if not body:
                continue
            events.append({"at": r.get("submitted_at"), "who": login, "kind": "review",
                           "state": r.get("state"), "body": body[:800], "url": r.get("html_url")})
        for c in paginated(f"repos/{repo}/issues/{n}/comments?per_page=100"):
            login = (c.get("user") or {}).get("login") or ""
            if BOTS.search(login) or login == author:
                continue
            events.append({"at": c.get("created_at"), "who": login, "kind": "comment",
                           "state": None, "body": (c.get("body") or "")[:800], "url": c.get("html_url")})
        if not events:
            continue
        # any author activity after the newest human event?
        last = max(e["at"] for e in events)
        answered = any(
            ((c.get("user") or {}).get("login") == author) and (c.get("created_at") or "") > last
            for c in paginated(f"repos/{repo}/issues/{n}/comments?per_page=100")
        )
        out.append({"repo": repo, "pr": n, "head": pr["head"]["sha"][:8], "title": pr["title"],
                    "events": events, "last_human": last, "answered_after": answered})
    return out


def main():
    repos = [r for r in os.environ.get("PR_REPOS", "").split(",") if r] or ["storytold/gridcraft"]
    if "--repos" in sys.argv:
        repos = sys.argv[sys.argv.index("--repos") + 1].split(",")
    rows = []
    for repo in repos:
        rows.extend(scan(repo, AUTHOR))
    need = [r for r in rows if not r["answered_after"]]
    print(f"{len(rows)} PRs with human activity, {len(need)} not answered since\n")
    for r in need:
        print(f"## {r['repo'].split('/')[1]} #{r['pr']} {r['head']} — {r['title'][:70]}")
        for e in r["events"][-3:]:
            print(f"   [{e['kind']}{'/' + e['state'] if e['state'] else ''}] {e['at']} {e['who']}: "
                  f"{' '.join(e['body'].split())[:220]}")
        print()
    if "--json" in sys.argv:
        print(json.dumps(need, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
