#!/usr/bin/env python3
"""Compact live state for one pull request.

Reports the things the loop actually gates on, and nothing that needs interpretation:

  * CodeRabbit counts, aggregated across every review posted on the *current head* (an empty stub
    review posted after a real one must not hide its findings);
  * unresolved review threads with their GraphQL ids (needed to resolve them);
  * human reviews and comments (excluding bots and yourself);
  * CI failures and pending checks on the head;
  * mergeability.

Usage:
    python prstate.py <PR> [--repo OWNER/NAME]

Repo resolution: --repo, else $PR_REPO, else the repository of the current directory.
Requires `gh` authenticated for the host you are targeting.
"""
import json
import os
import re
import subprocess
import sys

BOTS = ("coderabbitai", "coderabbitai[bot]")


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


def resolve_repo(flag):
    if flag:
        return flag
    if os.environ.get("PR_REPO"):
        return os.environ["PR_REPO"]
    return gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").strip()


def count(patterns, bodies):
    best = 0
    for body in bodies:
        for pat in patterns:
            for m in re.finditer(pat, body):
                best = max(best, int(m.group(1)))
    return best


FIELDS = """headRefOid mergeable mergeStateStatus
reviews(last:50){nodes{author{login} state body submittedAt commit{oid}}}
reviewThreads(first:100){nodes{id isResolved isOutdated path line
  comments(first:40){nodes{id author{login} path line body createdAt url}}}}"""


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not argv:
        print(__doc__)
        return 2
    pr = int(argv[0])
    repo = resolve_repo(None)
    if "--repo" in sys.argv:
        repo = sys.argv[sys.argv.index("--repo") + 1]
    if not repo:
        print(json.dumps({"pr": pr, "error": "no repo (pass --repo or PR_REPO)"}))
        return 2

    me = gh("api", "user", "-q", ".login").strip()
    skip = set(BOTS) | ({me} if me else set())

    pull = ghj("api", f"repos/{repo}/pulls/{pr}")
    if not pull:
        print(json.dumps({"pr": pr, "repo": repo, "error": "not found"}))
        return 1
    sha = pull["head"]["sha"]
    owner, name = repo.split("/")

    q = (
        'query{repository(owner:"%s",name:"%s"){pullRequest(number:%d){%s}}}'
        % (owner, name, pr, FIELDS)
    )
    g = ghj("api", "graphql", "-f", f"query={q}")
    g = ((g.get("data") or {}).get("repository") or {}).get("pullRequest") or {}

    runs = (ghj("api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100") or {}).get("check_runs", [])
    reviews = [r for r in (g.get("reviews", {}).get("nodes") or [])
               if (r.get("author") or {}).get("login") in BOTS]
    on_head = [r for r in reviews if (r.get("commit") or {}).get("oid") == sha]
    bodies = [r.get("body") or "" for r in on_head]
    newest = max(reviews, key=lambda r: r.get("submittedAt") or "") if reviews else None

    cr_runs = [c for c in runs if c.get("name", "").lower().startswith("coderabbit")]
    bad = re.compile(r"rate limit|paused|skipped|already reviewed", re.I)
    cr_final = bool(cr_runs) and all(
        c.get("status") == "completed"
        and c.get("conclusion") == "success"
        and not bad.search(((c.get("output") or {}).get("title") or "")
                           + ((c.get("output") or {}).get("summary") or ""))
        for c in cr_runs
    )

    if not bodies and not cr_final and newest:
        bodies = [newest.get("body") or ""]

    actionable = count([r"Actionable comments posted:\s*(\d+)"], bodies)
    outside = count([r"Outside diff range comments \((\d+)\)"], bodies)
    nitpicks = count([r"(?:Nitpick comments|Nitpicks):\s*(\d+)"], bodies)
    additional = count([r"Additional comments:\s*(\d+)"], bodies)

    threads = []
    for t in (g.get("reviewThreads", {}).get("nodes") or []):
        cs = t["comments"]["nodes"]
        if not cs:
            continue
        logins = {(c.get("author") or {}).get("login") for c in cs}
        if not (logins & set(BOTS)) and not (logins - skip):
            continue
        threads.append({
            "id": t["id"], "resolved": t["isResolved"], "outdated": t["isOutdated"],
            "path": t.get("path"), "line": t.get("line"),
            "authors": sorted(x for x in logins if x),
            "last": (cs[-1].get("body") or "")[:1500], "url": cs[-1].get("url"),
        })

    human_reviews = []
    for r in (g.get("reviews", {}).get("nodes") or []):
        login = (r.get("author") or {}).get("login")
        if login in skip:
            continue
        human_reviews.append({"author": login, "state": r.get("state"), "at": r.get("submittedAt"),
                              "on_head": (r.get("commit") or {}).get("oid") == sha,
                              "body": (r.get("body") or "")[:4000]})

    human = []
    for c in paginated(f"repos/{repo}/issues/{pr}/comments?per_page=100"):
        login = (c.get("user") or {}).get("login")
        if login in skip:
            continue
        human.append({"kind": "issue", "author": login, "at": c.get("created_at"),
                      "url": c.get("html_url"), "body": (c.get("body") or "")[:1500]})
    for c in paginated(f"repos/{repo}/pulls/{pr}/comments?per_page=100"):
        login = (c.get("user") or {}).get("login")
        if login in skip:
            continue
        human.append({"kind": "review", "author": login, "at": c.get("created_at"),
                      "url": c.get("html_url"), "path": c.get("path"),
                      "body": (c.get("body") or "")[:1500]})

    cross = [{"number": (e.get("source") or {}).get("issue", {}).get("number"),
              "title": (e.get("source") or {}).get("issue", {}).get("title"),
              "state": (e.get("source") or {}).get("issue", {}).get("state")}
             for e in paginated(f"repos/{repo}/issues/{pr}/timeline?per_page=100")
             if e.get("event") == "cross-referenced"]

    out = {
        "pr": pr, "repo": repo, "head": sha, "branch": pull["head"]["ref"],
        "mergeable": pull.get("mergeable"), "merge_state": pull.get("mergeable_state"),
        "cr": {
            "on_head": bool(on_head) or cr_final,
            "run_final": cr_final,
            "newest_review_at": (newest or {}).get("submittedAt"),
            "actionable": actionable, "outside_diff": outside,
            "nitpicks": nitpicks, "additional": additional,
        },
        "unresolved_threads": [t for t in threads if not t["resolved"]],
        "resolved_threads": len([t for t in threads if t["resolved"]]),
        "human_reviews": human_reviews,
        "human_comments": human,
        "cross_refs": [c for c in cross if c.get("number")],
        "ci": {
            "total": len(runs),
            "failed": [{"name": c["name"], "url": c["html_url"]} for c in runs
                       if c.get("conclusion") in ("failure", "timed_out", "cancelled", "action_required")],
            "pending": [c["name"] for c in runs if c.get("status") != "completed"],
        },
    }
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
