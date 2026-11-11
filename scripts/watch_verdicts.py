#!/usr/bin/env python3
"""Exit when every watched PR has a final CodeRabbit verdict on its head (or one has findings).

Usage: python watch_verdicts.py storytold/deckcraft:1836 storytold/deckcraft:1838 [--interval 300] [--hours 6]
"""
import importlib.util
import json
import os
import sys
import time

PRSTATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prstate.py")
spec = importlib.util.spec_from_file_location("prstate", PRSTATE)
prstate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prstate)


def main():
    args = [a for a in sys.argv[1:] if ":" in a]
    interval = 300
    hours = 6
    if "--interval" in sys.argv:
        interval = int(sys.argv[sys.argv.index("--interval") + 1])
    if "--hours" in sys.argv:
        hours = float(sys.argv[sys.argv.index("--hours") + 1])
    targets = [(a.rsplit(":", 1)[0], int(a.rsplit(":", 1)[1])) for a in args]
    deadline = time.time() + hours * 3600
    while time.time() < deadline:
        rows, settled = [], True
        for repo, pr in targets:
            try:
                d = prstate.gather(repo, pr)
            except Exception as e:
                rows.append({"repo": repo, "pr": pr, "error": str(e)})
                settled = False
                continue
            cr = d["cr"]
            findings = cr["actionable"] + cr["outside_diff"] + cr["additional"]
            final_head = cr["run_final"] and cr["on_head"] and not cr["paused"]
            row = {"repo": repo.split("/")[1], "pr": pr, "head": d["head"][:8],
                   "state": cr["review_state"], "findings": findings,
                   "threads": len(d["unresolved_threads"]), "final_on_head": final_head}
            rows.append(row)
            if not final_head or findings or row["threads"]:
                settled = False
        print(time.strftime("%H:%M:%S"), json.dumps(rows), flush=True)
        if settled:
            print("ALL SETTLED", flush=True)
            return 0
        if any(r.get("findings") for r in rows):
            print("ACTION", flush=True)
            return 0
        time.sleep(interval)
    print("TIMEOUT", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
