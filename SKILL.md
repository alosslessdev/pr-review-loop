---
name: pr-review-loop
description: Drive open pull requests to a machine-checked clean state — CodeRabbit findings, CI failures, merge conflicts and human review comments — with per-PR subagent loops, a shared findings registry, and a stacked-PR split playbook. Use when asked to resolve review comments on one or more PRs, to keep a PR green until review is done, or to split a monolithic PR into reviewable pieces.
metadata:
  version: "4"
---

# PR review loop

The loop exists because "address the review comments" is not a task with an end; it is a state to
reach and hold. This skill defines that state precisely, the polling cadence, and the coordination
rules that stop several agents from fixing the same defect twice.

## 1. Define the stop condition before starting

A PR is done only when **all** hold, re-read from the live API rather than remembered:

- CodeRabbit: `0 actionable`, `0 outside-diff`, `0 additional/unverified` findings on the **current
  head**, and `0` unresolved review threads.
- CodeRabbit ran on that head: `cr.review_state` is `final` — check run `completed` with conclusion
  `success` and a title like `Finished` / `Review completed`. `skipped`, `neutral`,
  `"Review rate limited"`, `"Review paused"` do **not** count; a paused series means nobody will ever
  review that head until `@coderabbitai resume` is posted.
- CI green on that head (no failed, no pending). A fork PR whose workflows sit in `action_required`
  is *not* a failure — record the local gate as evidence instead.
- `mergeable` true with no conflicts against `main`.
- Every human review/comment addressed: implemented, or answered with the commit that already does it.

Nitpicks **never** block. A stale count in a review body whose threads are all resolved is not work —
say so in the PR body and move on.

**Outside-diff findings do not reappear.** They carry no thread, so a later review that only sees the
changed files drops them from its counts while the defect is still in the code. Keep a ledger (the
registry, or a PR comment) of every outside-diff and additional finding raised on the branch, and
close each one by name — commit plus the sentence saying what changed. "The latest review says 0" is
not evidence that an earlier body-only finding was fixed; check the code.

## 1b. Two gates that are not CodeRabbit

**The maintainer's own gate.** A repo usually has a documented quality command —
`cargo xtask ci`, `make check`, `npm run ci`. Run it **in full and early**, not "the touched crates
only". A partial run is not evidence, and reviewers block on exactly that:

> `cargo xtask ci` is incomplete or unchecked on this PR. Run it end to end (fmt, clippy, tests,
> assets, brands, layers, wasm), tick the checklist when green, and reply here with the outcome.

When a PR template carries a checklist, tick only what actually passed this run, leave anything
pre-existing unticked with its evidence beside it, and answer the review with the commands, per-step
exit codes and head SHA. Never tick an item on a partial run; never mark a step green because it
"should" pass.

**Human review state.** `CHANGES_REQUESTED` is a blocking gate even when every CodeRabbit count is
zero. Read `reviewDecision` and each human review's state every iteration; an `APPROVED` from before
your last push is stale. Reply *to the review* (not just a PR comment) and address the reviewer.

**Fork CI is not green, it is absent.** A fork PR often shows only CodeRabbit in `gh pr checks`, with
the combined status `pending` because `total_count` is 0 and the workflow runs sit in
`action_required`. That means nothing ran. Say so explicitly, attach the local gate transcript, and
do not record it as a pass.

## 2. Loop mechanics

Iterate with a fixed interval (5 minutes is right: long enough for CI and CodeRabbit, short enough
to react). Each iteration:

1. Refresh state (see `scripts/prstate.py`).
2. Fix conflicts: `git fetch origin main && git merge origin/main`, resolve preserving both intents,
   fix merge fallout, run the gate, push. Never rebase or force-push a branch others build on.
3. Fix CodeRabbit findings — inline threads **and** body-only sections (`Outside diff range
   comments`, `Additional comments`, unverified findings). Reply in-thread with the fixing commit,
   then resolve the thread. A finding that is factually wrong gets an evidence-backed reply, then
   resolution.
4. Fix CI failures from their logs (`gh run view <id> --log-failed`), never by weakening a test.
5. Handle human reviews: implement each point, reply bullet-by-bullet, post `@<reviewer> ready`.
6. CodeRabbit liveness: skipped/paused/rate-limited means it will not re-review on its own. Only
   `@coderabbitai full review` re-triggers a review; `@coderabbitai review` is ignored. **A paused
   series needs `@coderabbitai resume` first** — CodeRabbit says "reviews paused" at the top of its
   summary and its check run reads `Review paused` / `Skipped`. `prstate.py` reports this as
   `cr.review_state: "paused"` plus a blocker; resume, then request the full review. In a stack one
   pause can stall every PR, so sweep them (`pr-backlog-audit`'s `resume_paused.py`).
7. Sleep the interval. Rate-limited waits are **free time** — they do not consume iterations.

Budgets exist only to stop runaway loops; when the user raises one, the loop continues. Never yield
because of budget or a request cap: leave a precise hand-off and keep the state recoverable.

Full protocol, including the exact wording to use on PRs: `references/loop-protocol.md`.

## 2b. Audit the whole backlog, not just the PR you were asked about

A vanished finding is invisible per-PR: you have to look for it. Before declaring a repo clean, use
the companion skill **pr-backlog-audit** (`audit_prs.py --repos OWNER/A,OWNER/B`), which walks every
open PR by the author, extracts each CodeRabbit body-only finding (outside-diff, additional), and
looks for evidence it was closed:

| status | meaning |
|---|---|
| `answered-with-commit` | a later comment names the path and a commit |
| `answered` | a later comment names the path (or the finding's title) |
| `likely` | the path changed in a commit after the finding — still worth a look |
| `needs-review` | no evidence at all; someone must read the code |

`needs-review` and `likely` rows are the backlog. Fix them, or close them with an evidence-backed
comment; do not let "0 findings on the latest review" stand in for "no open work".

## 3. Never trust a summary of state

`prstate.py` aggregates CodeRabbit counts across the reviews on the **current head** and reports
unresolved threads with their GraphQL ids, human reviews/comments, CI and mergeability. Two traps it
exists to avoid:

- CodeRabbit posts empty stub reviews after a real one; picking "the newest review" hides findings.
  Aggregate per head instead.
- A body count can outlive its threads. Cross-check `unresolved_threads` before believing
  `actionable: N`.

It also returns a `blockers` list — actionable/outside-diff/additional counts, unresolved threads,
`CHANGES_REQUESTED` reviews, failed checks, "no workflow ran on this head", merge conflicts, and
unchecked PR-template items. Gate on that list; it is the short form of everything a maintainer will
refuse to approve.

Verify claims about other PRs the same way — a sibling's "done" is a claim, not evidence.

## 4. Coordinate, do not duplicate

One agent per PR. When PRs share code (stacks, duplicated files), findings repeat across them:

- Maintain `scripts/findings-registry.md`-style rows: PR, file:line, finding, owning PR, status,
  commit. Claim before fixing; anyone else replies with the owner's commit and resolves.
- The owning slice fixes; every other PR points. Duplicate *code* is acceptable; duplicate *fixes*
  are not.
- Shared review quotas (CodeRabbit OSS) are a queue: one outstanding `full review` across the
  stack, ordered by dependency. Parallel requests starve everyone.
- Send a finding to its owner directly rather than writing a second implementation.

`references/coordination.md` has the registry format and the messaging pattern.

## 5. Splitting a monolithic PR

A 74k-line PR is not reviewable; the repo's own merged-PR median is ~75 lines. Split it so each PR
merges independently:

- Size each slice against what the repo actually merges (measure: median and max of recent merged
  PRs), not against what feels small.
- Prefer a **single prerequisite** (the crate/parser/type core) plus leaves that depend only on it,
  so out-of-order merges cost a red check, never a conflict.
- Prove slice independence mechanically: own files per branch (`git log --no-merges <base>..<branch>`
  minus the prerequisite's files) must be pairwise disjoint.
- Shared registry files (`eval.rs` dispatch arrays, `catalog.rs` rows) get one inert marker line per
  slice in the prerequisite, which each slice replaces — otherwise every pair of slices conflicts.
- Only the prerequisite may create the crate/`Cargo.toml` stanza. Every other PR shows only its own
  change; that is exactly what reviewers ask for on stacked PRs.
- Prove coverage before closing the monolith: the union of the split PRs' changed files must **equal**
  the monolith's file set. Close the monolith as superseded, with the split map as the closing
  comment, and post the same map on the tracking issue.

`references/stacked-pr-split.md` carries the full playbook, including the replay problem (fixes made
on the monolith after a slice forked) and why cherry-picks may be impossible.

## 6. Rules that keep the branch mergeable

- Read-only upstream: an author account often has `pull` only. Push to the fork, never to `main`, and
  never plan around "we will merge X first" — merging is the maintainer's action.
- No `unwrap()`/`expect()`/`panic!` outside tests, no `unsafe`; never break wasm builds.
- Every fix carries the test that would have caught it; prove it fails before the fix — per hunk.
  Check out the branch, revert one hunk at a time, and confirm the test that is supposed to cover
  that hunk goes red. A PR body that says "both tests fail without the fix" is often wrong by the
  time the branch has other fixes in it; correct the claim rather than repeating it.
- Regenerate generated docs (`cargo xtask parity`, keytips, i18n) rather than hand-editing counts. A
  Windows checkout with `core.autocrlf=true` can report a false "stale" — regenerate and confirm
  `git diff` is empty.
- Record each iteration in a log file per PR: head, commits, findings, gate results. Hand-offs are
  only useful if they are factual.

## 7. Do not

- Do not close a PR, force-push a shared branch, or delete a test to make CI pass.
- Do not treat a green check run from a prerequisite's commit as your PR's review.
- Do not let a "stable" report from another agent end your loop; re-read the live state.
- Do not read images on a runtime that cannot accept image input; assert on artifacts in tests.
- Do not accept a hand-off that contains only prose. If a run produced no diff, no commit and no
  PR state change, it did not happen: re-run it with the method rule "read → edit → run the smallest
  proving test", not with a fresh plan.
