#!/usr/bin/env python3
"""Wait for CodeRabbit (and CI) to settle on one or more PRs.

This is the waiter the loop runs OUTSIDE an agent. An agent that stops while
`cr.review_state` is `in_progress`, `none`, or `paused` has not finished; it has
simply stopped watching. Run this instead:

    python scripts/wait_verdict.py --repo storytold/gridcraft --pr 3685
    python scripts/wait_verdict.py --repos storytold/gridcraft,storytold/deckcraft
    python scripts/wait_verdict.py --repo storytold/gridcraft --pr 3685 --once

Exit 0 = every watched PR reached the requested state (default: a `final`
CodeRabbit verdict on its current head). Exit 2 = the iteration cap was hit;
that is a timeout, never a pass. Exit 1 = bad arguments / API failure.

Defaults are deliberately large: 20,000 iterations at 300 s is the loop's wait
budget, not a request budget. A subagent's ~200-request soft cap never
authorizes declaring a PR done; hand off to this waiter instead.
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prstate  # noqa: E402


def post(repo, pr, body):
    p = subprocess.run(["gh", "pr", "comment", str(pr), "--repo", repo, "--body", body],
                       capture_output=True, text=True, encoding="utf-8")
    return p.returncode == 0


def summarize(st):
    return {
        "head": st["head"][:8],
        "state": st["cr"]["review_state"],
        "paused": st["cr"]["paused"],
        "on_head": st["cr"]["on_head"],
        "actionable": st["cr"]["actionable"],
        "outside": st["cr"]["outside_diff"],
        "additional": st["cr"]["additional"],
        "unresolved": len(st["unresolved_threads"]),
        "pending": list(st["ci"]["pending"]),
        "failed": [c["name"] for c in st["ci"]["failed"]],
        "ci_absent": st["ci_absent"],
    }


def settled(s, until):
    """True when this PR no longer needs waiting."""
    if until == "final":
        return s["state"] == "final"
    # until == "clean": the loop's stop condition for review state.
    return (s["state"] == "final" and s["actionable"] == 0 and s["outside"] == 0
            and s["additional"] == 0 and s["unresolved"] == 0 and not s["pending"])


def describe(repo, pr, s):
    bits = [f"{s['state']}", f"head={s['head']}"]
    if s["paused"]:
        bits.append("PAUSED: post '@coderabbitai resume'")
    if s["failed"]:
        bits.append("failed=" + ",".join(s["failed"]))
    if s["pending"]:
        bits.append("pending=" + ",".join(s["pending"]))
    if s["state"] == "final":
        bits.append(f"{s['actionable']} actionable / {s['outside']} outside-diff / "
                    f"{s['additional']} additional / {s['unresolved']} unresolved")
    return f"{repo}#{pr}: " + "  ".join(bits)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="OWNER/NAME")
    ap.add_argument("--pr", action="append", default=[], help="PR number (repeatable / comma list)")
    ap.add_argument("--repos", help="comma-separated OWNER/NAME list; watches every open PR by --author")
    ap.add_argument("--author", default="alosslessdev")
    ap.add_argument("--interval", type=int, default=300, help="seconds between polls (default 300)")
    ap.add_argument("--max-iterations", type=int, default=20000,
                    help="hard cap, not a target (default 20000)")
    ap.add_argument("--until", choices=("final", "clean"), default="final",
                    help="final = verdict on head; clean = verdict + 0 findings + 0 unresolved + no pending")
    ap.add_argument("--once", action="store_true", help="one snapshot, then exit")
    ap.add_argument("--resume", action="store_true",
                    help="post '@coderabbitai resume' when a series is paused")
    a = ap.parse_args(argv)

    targets = []
    if a.repos:
        for repo in a.repos.split(","):
            nums = [p["number"] for p in
                    prstate.paginated(f"repos/{repo}/pulls?state=open&per_page=100")
                    if (p.get("user") or {}).get("login") == a.author]
            targets += [(repo, n) for n in nums]
    elif a.repo:
        nums = [int(n) for chunk in a.pr for n in chunk.split(",") if n]
        if not nums:
            nums = [p["number"] for p in
                    prstate.paginated(f"repos/{a.repo}/pulls?state=open&per_page=100")
                    if (p.get("user") or {}).get("login") == a.author]
        targets = [(a.repo, n) for n in nums]
    else:
        ap.error("pass --repo OWNER/NAME (with --pr) or --repos OWNER/A,OWNER/B")
    if not targets:
        print("no PRs to watch", file=sys.stderr)
        return 1

    print(f"watching {len(targets)} PR(s): until={a.until} interval={a.interval}s "
          f"max_iterations={a.max_iterations}", flush=True)

    last = {}
    for i in range(1, a.max_iterations + 1):
        states, all_settled = [], True
        for repo, pr in targets:
            st = prstate.gather(repo, pr)
            if "error" in st:
                states.append((repo, pr, None))
                all_settled = False
                continue
            s = summarize(st)
            states.append((repo, pr, s))
            if a.resume and s["paused"] and not a.once:
                post(repo, pr, "@coderabbitai resume")
            if not settled(s, a.until):
                all_settled = False

        for repo, pr, s in states:
            key = (repo, pr)
            line = f"{repo}#{pr}: state unavailable" if s is None else describe(repo, pr, s)
            if last.get(key) != line:
                print(f"[{i}/{a.max_iterations}] {line}", flush=True)
                last[key] = line

        if all_settled:
            print(f"settled after {i} iteration(s): every watched PR reached '{a.until}'", flush=True)
            return 0
        if a.once:
            print("not settled (--once)", flush=True)
            return 2
        time.sleep(a.interval)

    print(f"TIMEOUT after {a.max_iterations} iterations: not settled. "
          "This is not a pass; leave the hand-off and re-run.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
