# pr-review-loop

A skill for driving open pull requests to a machine-checked clean state: CodeRabbit findings, CI
failures, merge conflicts and human review comments, with per-PR subagent loops, a shared findings
registry, and a playbook for splitting a monolithic PR into reviewable slices.

It is written for agent workflows (Claude Code, omp, Codex, Cursor and similar), but the protocol is
just GitHub + `gh` CLI, so it works for a human reviewer too.

## Install

```bash
git clone https://github.com/<owner>/pr-review-loop.git
cp -r pr-review-loop ~/.agents/skills/pr-review-loop        # omp / generic agent skills dir
# or
cp -r pr-review-loop .claude/skills/pr-review-loop          # Claude Code project skills
```

The skill is `SKILL.md` plus `references/` and `scripts/prstate.py`. It assumes `gh` is authenticated
for the host you target.

## Use

Ask the agent to resolve the review comments on one or more PRs, e.g.

> Resolve all CodeRabbit comments and CI failures on these PRs, one agent per PR, and keep each PR
> green until review is complete.

The agent reads `SKILL.md`, then `references/loop-protocol.md`, and runs `scripts/prstate.py` to see
the live gate state.

```bash
python scripts/prstate.py 1234 --repo owner/name      # one PR: state + blockers
python scripts/audit_prs.py --repo owner/name         # every open PR: findings that were never closed
```

## What makes this different from "please fix the review"

- **An explicit stop condition.** Zero actionable / outside-diff / additional findings on the current
  head, zero open threads, a CodeRabbit run that completed `success` on that head, CI green,
  mergeable, human asks answered. Nitpicks never block.
- **Blockers, not vibes.** `prstate.py` returns a `blockers` list — CodeRabbit counts, unresolved
  threads, `CHANGES_REQUESTED` reviews, failed checks, "no workflow ran on this head", merge
  conflicts, and unchecked PR-template items.
- **State is aggregated, not guessed.** CodeRabbit posts empty stub reviews after a real one; picking
  "the newest review" hides findings. `prstate.py` aggregates per head and cross-checks unresolved
  threads, so a stale body count is not mistaken for work.
- **Skipped ≠ clean.** `skipped`, `paused`, `rate limited` and "already reviewed" all mean the bot
  will not re-review on its own; only `@coderabbitai full review` re-triggers it.
- **One owner per defect.** Findings that repeat across stacked PRs are claimed in a registry before
  being fixed; everyone else points at the owner's commit. Duplicate code is acceptable, duplicate
  fixes are not.
- **Shared quotas are a queue.** Ten PRs requesting reviews in parallel starve each other; requests
  go out one at a time, dependency order first.
- **The repo's gate is the gate.** Run the documented quality command end to end (`cargo xtask ci`
  or equivalent), record per-step exit codes, tick only genuinely passing checklist items, and answer
  the blocking review with that evidence. A partial run is not evidence.
- **Splitting is mechanical, not vibes.** Size slices against the repository's real merged-PR ceiling;
  prove own-file disjointness, coverage equality, and "only the prerequisite creates the crate" before
  opening anything; close the monolith as superseded with the split map.

## Contents

| Path | What |
|---|---|
| `SKILL.md` | the skill: stop condition, loop mechanics, coordination, split rules |
| `references/loop-protocol.md` | full per-iteration protocol, commands, wording, failure modes |
| `references/stacked-pr-split.md` | how to cut a 180-file PR into ten mergeable PRs, with the invariant checks |
| `references/coordination.md` | findings registry, shared quotas, fan-out discipline, hand-offs |
| `scripts/prstate.py` | compact live state for one PR (head, findings, threads, review decision, CI, blockers) |
| `scripts/audit_prs.py` | backlog audit across open PRs: every body-only finding with closure evidence |

## Licence

MIT.
