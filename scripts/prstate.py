#!/usr/bin/env python3
"""Compact live state for one pull request.

Reports the things the loop actually gates on, and nothing that needs interpretation:

  * CodeRabbit counts, aggregated across every review posted on the *current head* (an empty stub
    review posted after a real one must not hide its findings);
  * unresolved review threads with their GraphQL ids (needed to resolve them);
  * human reviews and comments (excluding bots and yourself);
  * CI failures and pending checks on the head;
  * mergeability, the GitHub review decision, and whether CI actually ran on this fork PR.

Anything that blocks a maintainer's approval is collected in `blockers`, so the loop can gate on a
short list instead of re-deriving it from counts.

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


FIELDS = """headRefOid mergeable mergeStateStatus reviewDecision
reviews(last:50){nodes{author{login} state body submittedAt commit{oid}}}
reviewThreads(first:100){nodes{id isResolved isOutdated path line
  comments(first:40){nodes{id author{login} path line body createdAt url}}}}"""


def gather(repo, pr, me=None):
    """The state dict for one PR. Importable: audit tools build on this, not on argv."""
    if me is None:
        me = gh("api", "user", "-q", ".login").strip()
    skip = set(BOTS) | ({me} if me else set())

    pull = ghj("api", f"repos/{repo}/pulls/{pr}")
    if not pull:
        return {"pr": pr, "repo": repo, "error": "not found"}
    sha = pull["head"]["sha"]
    owner, name = repo.split("/")

    q = (
        'query{repository(owner:"%s",name:"%s"){pullRequest(number:%d){%s}}}'
        % (owner, name, pr, FIELDS)
    )
    g = ghj("api", "graphql", "-f", f"query={q}")
    g = ((g.get("data") or {}).get("repository") or {}).get("pullRequest") or {}

    runs = (ghj("api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100") or {}).get("check_runs", [])
    suites = (ghj("api", f"repos/{repo}/commits/{sha}/check-suites") or {}).get("check_suites", [])
    statuses = ghj("api", f"repos/{repo}/commits/{sha}/status") or {}
    wf_runs = (ghj("api", f"repos/{repo}/actions/runs?head_sha={sha}&per_page=50") or {}).get("workflow_runs", [])
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

    # A paused review series stalls every PR in the stack: CodeRabbit says "reviews paused" at the
    # top of its summary and its check run reads "Review paused" / "Skipped". Only
    # `@coderabbitai resume` restarts it.
    pause_re = re.compile(r"paused|rate limit|review skipped|reviews (?:are )?paused", re.I)
    run_paused = any(pause_re.search(((c.get("output") or {}).get("title") or "")
                                     + ((c.get("output") or {}).get("summary") or ""))
                     for c in cr_runs if c.get("status") == "completed")
    cr_issues = [c for c in paginated(f"repos/{repo}/issues/{pr}/comments?per_page=100")
                 if (c.get("user") or {}).get("login") in BOTS]
    comment_paused = any(pause_re.search(c.get("body") or "") for c in cr_issues[-6:]) if cr_issues else False
    in_progress = any(c.get("status") != "completed" for c in cr_runs)
    review_state = ("final" if cr_final else
                    "in_progress" if in_progress else
                    "paused" if (run_paused or comment_paused) else "none")
    if not bodies and review_state == "none" and newest:
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

    body = pull.get("body") or ""
    checklist = {
        "total": len(re.findall(r"^- \[ \]", body, re.M)) + len(re.findall(r"^- \[x\]", body, re.M | re.I)),
        "unchecked": len(re.findall(r"^- \[ \]", body, re.M)),
    }

    review_decision = (g.get("reviewDecision") or "").strip()
    # Only the *latest* review per author counts: an earlier CHANGES_REQUESTED is superseded by a
    # later APPROVED (or by the same reviewer re-reviewing the new head).
    latest_by_author = {}
    for r in sorted(human_reviews, key=lambda r: r.get("at") or ""):
        latest_by_author[(r.get("author") or "").lower()] = r
    blocking_reviews = [r for r in latest_by_author.values()
                        if (r.get("state") or "").upper() == "CHANGES_REQUESTED"]
    repo_checks = [c for c in runs if not c.get("name", "").lower().startswith("coderabbit")]
    wf_planned = [w for w in wf_runs if (w.get("conclusion") or "") == "action_required"]
    ci_absent = not repo_checks and (not wf_runs or len(wf_planned) == len(wf_runs))

    cross = [{"number": (e.get("source") or {}).get("issue", {}).get("number"),
              "title": (e.get("source") or {}).get("issue", {}).get("title"),
              "state": (e.get("source") or {}).get("issue", {}).get("state")}
             for e in paginated(f"repos/{repo}/issues/{pr}/timeline?per_page=100")
             if e.get("event") == "cross-referenced"]

    out = {
        "pr": pr, "repo": repo, "head": sha, "branch": pull["head"]["ref"],
        "mergeable": pull.get("mergeable"), "merge_state": pull.get("mergeable_state"),
        "cr": {
            "review_state": review_state,
            "paused": review_state == "paused",
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
        "review": {
            "decision": review_decision or ("CHANGES_REQUESTED" if blocking_reviews else None),
            "blocking_reviews": [{"author": r["author"], "at": r["at"]} for r in blocking_reviews],
        },
        "checklist": checklist,
        "ci_absent": ci_absent,
        "blockers": [],
        "ci": {
            "total": len(runs),
            "failed": [{"name": c["name"], "url": c["html_url"]} for c in runs
                       if c.get("conclusion") in ("failure", "timed_out", "cancelled", "action_required")],
            "pending": [c["name"] for c in runs if c.get("status") != "completed"],
        },
    }
    b = out["blockers"]
    if actionable or outside or additional:
        b.append(f"coderabbit: {actionable} actionable / {outside} outside-diff / {additional} additional")
    if out["cr"]["paused"]:
        b.append("coderabbit: reviews paused on this head (post '@coderabbitai resume')")
    if out["unresolved_threads"]:
        b.append(f"threads: {len(out['unresolved_threads'])} unresolved")
    if blocking_reviews:
        b.append("review: CHANGES_REQUESTED by " + ", ".join(r["author"] for r in blocking_reviews))
    if out["ci"]["failed"]:
        b.append("ci: " + ", ".join(c["name"] for c in out["ci"]["failed"]))
    if ci_absent:
        b.append("ci: no workflow ran on this head (action_required / empty status)")
    if pull.get("mergeable") is False:
        b.append("merge: conflicts with the base branch")
    if checklist["unchecked"]:
        b.append(f"checklist: {checklist['unchecked']} unchecked item(s) in the PR body")

    return out


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
    out = gather(repo, pr)
    print(json.dumps(out, indent=1))
    return 0 if "error" not in out else 1


if __name__ == "__main__":
    sys.exit(main())
