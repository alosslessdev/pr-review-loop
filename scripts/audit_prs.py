#!/usr/bin/env python3
"""Audit open PRs for findings that were raised and never closed.

CodeRabbit body-only findings (Outside diff range, Additional comments) carry no thread. A later
review that only sees the changed files drops them from its counts, so "the latest review says 0" is
not evidence that they were fixed. This script finds every such finding on a PR, then looks for
evidence that it was closed:

  answered-with-commit  a comment posted after the finding names the path and a commit SHA
  answered              a comment posted after the finding names the path
  likely                the path changed in a commit made after the finding
  needs-review          none of the above — someone must read the code

Usage:
    python audit_prs.py --repo storytold/gridcraft [--author alosslessdev] [--pr 3606] [--json]

Requires `gh` authenticated for the host.
"""
import json
import os
import re
import subprocess
import sys

OUTSIDE = re.compile(r"Outside diff range comments \((\d+)\)")
ADDITIONAL = re.compile(r"Additional comments:\s*(\d+)")
ITEM = re.compile(
    r"<summary>.*?</picture>\s*(?P<sev>Critical|Major|Minor|Trivial)\s*</strong>\s*·\s*"
    r"(?P<title>[^<]+?)\s*·\s*<code>(?P<loc>[^<]+)</code>",
    re.S,
)
SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


def gh(*args):
    p = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    return p.stdout if p.returncode == 0 else ""


def ghj(*args):
    try:
        return json.loads(gh(*args) or "{}")
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


def body_findings(review):
    """[(severity, title, path, line)] for the body-only findings of one review."""
    body = review.get("body") or ""
    if not (OUTSIDE.search(body) or ADDITIONAL.search(body)):
        return []
    out = []
    for m in ITEM.finditer(body):
        loc = m.group("loc").strip()
        path, _, line = loc.rpartition(":")
        if not path:
            path, line = loc, ""
        out.append({
            "severity": m.group("sev").strip(),
            "title": " ".join(m.group("title").split()),
            "path": path.strip().strip("`"),
            "line": line.strip().strip("`"),
        })
    return out


def audit(repo, pr, author):
    pull = ghj("api", f"repos/{repo}/pulls/{pr}")
    if not pull:
        return None
    head = pull["head"]["sha"]
    reviews = [r for r in paginated(f"repos/{repo}/pulls/{pr}/reviews?per_page=100")
               if (r.get("user") or {}).get("login") in ("coderabbitai", "coderabbitai[bot]")]
    comments = paginated(f"repos/{repo}/pulls/{pr}/comments?per_page=100")
    issues = paginated(f"repos/{repo}/issues/{pr}/comments?per_page=100")
    convo = [{"at": c.get("created_at"), "body": c.get("body") or "", "url": c.get("html_url")}
             for c in comments] + \
            [{"at": c.get("created_at"), "body": c.get("body") or "", "url": c.get("html_url")}
             for c in issues if (c.get("user") or {}).get("login") != "coderabbitai"]

    compares = {}
    def changed_since(commit):
        if commit not in compares:
            files = set()
            cmp = ghj("api", f"repos/{repo}/compare/{commit}...{head}")
            for f in cmp.get("files") or []:
                files.add(f.get("filename") or "")
            compares[commit] = files
        return compares[commit]

    findings = []
    for r in reviews:
        raised = r.get("submitted_at") or ""
        commit = (r.get("commit") or {}).get("oid") or ""
        for f in body_findings(r):
            later = [c for c in convo if (c["at"] or "") > raised]
            by_path = [c for c in later if f["path"] and f["path"] in c["body"]]
            title_key = " ".join(f["title"].lower().split()[:4])
            by_title = [c for c in later if title_key and title_key in " ".join(c["body"].lower().split())]
            evidence = None
            status = "needs-review"
            if by_path and any(SHA.search(c["body"]) for c in by_path):
                status, evidence = "answered-with-commit", by_path[0]["url"]
            elif by_path:
                status, evidence = "answered", by_path[0]["url"]
            elif by_title:
                status, evidence = "answered", by_title[0]["url"]
            elif commit and f["path"] in changed_since(commit):
                status = "likely"
            findings.append({
                "pr": pr, "repo": repo, "raised_at": raised, "review_commit": commit[:8],
                "on_head": commit == head, **f, "status": status, "evidence": evidence,
            })

    return {
        "pr": pr, "repo": repo, "head": head[:8], "author": (pull.get("user") or {}).get("login"),
        "title": pull.get("title"), "mergeable": pull.get("mergeable"),
        "findings": findings,
        "open_threads": None,  # filled by prstate when needed
    }


def main():
    argv = sys.argv[1:]
    repo = argv[argv.index("--repo") + 1] if "--repo" in argv else ""
    author = argv[argv.index("--author") + 1] if "--author" in argv else "alosslessdev"
    only = int(argv[argv.index("--pr") + 1]) if "--pr" in argv else None
    as_json = "--json" in argv
    if not repo:
        print(__doc__)
        return 2

    nums = [only] if only else [
        p["number"] for p in paginated(f"repos/{repo}/pulls?state=open&per_page=100")
        if (p.get("user") or {}).get("login") == author
    ]
    results = [audit(repo, n, author) for n in nums]
    results = [r for r in results if r]

    if as_json:
        print(json.dumps(results, indent=1))
        return 0

    total = sum(len(r["findings"]) for r in results)
    open_findings = [f for r in results for f in r["findings"] if f["status"] == "needs-review"]
    print(f"# body-only findings audit — {repo}\n")
    print(f"{len(results)} open PRs, {total} body-only findings, "
          f"{len(open_findings)} needing a code check\n")
    hdr = "| PR | raised | severity | finding | path | status |"
    print(hdr)
    print("|---|---|---|---|---|---|")
    for r in sorted(results, key=lambda r: r["pr"]):
        for f in r["findings"]:
            print(f"| #{f['pr']} | {f['raised_at'][:16]} | {f['severity']} | {f['title'][:70]} | "
                  f"`{f['path']}:{f['line']}` | {f['status']} |")
    if open_findings:
        print("\n## needs a code check\n")
        for f in open_findings:
            print(f"- #{f['pr']} `{f['path']}:{f['line']}` — {f['title']} "
                  f"(raised {f['raised_at'][:16]} on {f['review_commit']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
