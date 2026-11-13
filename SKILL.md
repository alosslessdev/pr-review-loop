---
name: pr-review-loop
description: Drive open pull requests to a machine-checked clean state — CodeRabbit findings, CI failures, merge conflicts and human review comments — with per-PR subagent loops, a shared findings registry, and a stacked-PR split playbook. Use when asked to resolve review comments on one or more PRs, to keep a PR green until review is done, or to split a monolithic PR into reviewable pieces.
metadata:
  version: "16"
---

# PR review loop

The loop exists because "address the review comments" is not a task with an end; it is a state to
reach and hold. This skill defines that state precisely, the polling cadence, and the coordination
rules that stop several agents from fixing the same defect twice.

## 1. Define the stop condition before starting

A PR is done when — and only when — **two things are true**, re-read from the live API rather than
remembered:

1. **Every GitHub check on the current head is green** — the repo's `gate`, CodeRabbit, SonarCloud,
   and any other workflow. **`skipped` is fine** — what matters is that every check which actually
   *ran* is green. `pending` is not green (wait it out), `action_required` is absent rather than green
   (fork PRs need maintainer approval), and a check that failed once must be fixed, not re-run until it
   passes.
2. **CodeRabbit has been looped to clean** — all of:
   - every actionable comment addressed (0 actionable, 0 outside-diff, 0 additional on the head);
   - no new ones after the last review: the newest review is *on the current head*, so nothing is
     waiting to be reviewed;
   - reviews are **unpaused and settled**: `review_state: final` on the current head.
     `in_progress` is a WAIT state, never a terminal one — run `scripts/wait_verdict.py`;
     `paused` needs `@coderabbitai resume` first;
   - all threads resolved, each reply naming its fixing commit;
   - **nitpick comments are ignored** — they never block, never need a reply, and are not counted.
   Plus every human review/comment answered.

**A `pending`, `in_progress`, `none`, or `paused` review state is never "done".** The waiter is
`scripts/wait_verdict.py` (default **20,000 iterations**). Do not end a run, report clean, or hand off
as finished while it reports `in_progress`: the verdict has not landed. A subagent's ~200-request soft
cap is a trigger to hand off to the waiter or respawn — never a reason to call the PR clean.

Mergeability, conflicts with `main`, and whether a maintainer has merged are **not** part of "done" —
see the note on mergeable below. Everything below elaborates those two points:

- CodeRabbit: `0 actionable`, `0 outside-diff`, `0 additional/unverified` findings on the **current
  head**, and `0` unresolved review threads.
- CodeRabbit ran on that head: `cr.review_state` is `final` — check run `completed` with conclusion
  `success` and a title like `Finished` / `Review completed`. `skipped`, `neutral`,
  `"Review rate limited"`, `"Review paused"` do **not** count; a paused series means nobody will ever
  review that head until `@coderabbitai resume` is posted.
- CI green on that head (no failed, no pending). A fork PR whose workflows sit in `action_required`
  is *not* a failure — record the local gate as evidence instead.
- Mergeable is **information, not a gate** — for code *and* docs. Both move every few hours here, so a
  dirty branch is expected state and chasing it is busywork that produces a new head and discards a
  green verdict. Merge only when a push is actually rejected, when CI cannot run without it, or when a
  maintainer asks. Never let a conflict keep a loop alive.
- **The one hard exception: a conflicted PR runs no CI at all.** GitHub does not build the synthetic
  merge ref for a `CONFLICTING` PR, so `pull_request` workflows are never created — there is no run to
  be `action_required`, none to fail, none to approve. `gh pr checks` comes back empty and every
  "no failed, no pending" reading is vacuously true while nothing has been tested. Verify with
  `gh api repos/O/R/actions/runs?head_sha=<sha>` (empty = never ran) and `gh pr view N --json mergeable`.
  When a branch is conflicted *and* you need a measured check (a coverage/duplication gate, a test that
  has never run on the head), resolving is the only way to obtain evidence: merge `origin/main`,
  resolve preserving both intents, keep the merge commit separate from feature work, and say in the PR
  why. Resolve for no other reason.
- Every human review/comment addressed: implemented, or answered with the commit that already does it.

Nitpicks **never** block. A stale count in a review body whose threads are all resolved is not work —
say so in the PR body and move on.

**"Done" is a measurement on a head, never a conclusion you carry forward.** A verdict can land
seconds after you report clean — this session produced two PRs declared green that immediately took 3
and 2 new findings from a review already in flight. Never report from remembered counts: re-read
`prstate.py` for the exact SHA you are about to name, and treat any review timestamped after your last
push as new work. Equally, a body-only `Additional comments: N` or `Out of Scope Changes` section is
work — answer it with a commit or with `file:line` evidence; do not let it sit because its thread
count is 0.

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
zero — but only the **latest review per author** counts: an earlier request for changes is superseded
when the same reviewer later approves or re-reviews. A tool that flags every historical
`CHANGES_REQUESTED` will report three "blockers" on PRs that were approved weeks ago. Read
`reviewDecision` where the repo enforces reviews, and otherwise take the newest review per reviewer.
An `APPROVED` from before your last push is stale in the other direction. Reply *to the review* (not just a PR comment) and address the reviewer.

**Fork CI is not green, it is absent.** A fork PR often shows only CodeRabbit in `gh pr checks`, with
the combined status `pending` because `total_count` is 0 and the workflow runs sit in
`action_required`. That means nothing ran. Say so explicitly, attach the local gate transcript, and
do not record it as a pass.

**A check that "runs" can also decline to do its job.** The SonarCloud workflow completes `success`
while its log says `SONAR_TOKEN is not available (e.g. a fork pull request); skipping SonarCloud
scan` — the app check then keeps reporting a number **pinned to an older SHA**. Two consequences:
- A red Sonar/coverage number may be *stale*, not measured on your head. Read the workflow log and the
  analysis timestamp before believing it; `11.4% duplication` measured five commits ago is not a
  finding about this code.
- Such a gate cannot be turned green from a fork PR. Record it as an infra blocker with the log line as
  evidence, never as green, and never "fix" it by editing repo-wide quality config (`sonar.exclusions`,
  `cpd.exclusions`) without an explicit user decision — propose the numbers and let them choose.

**Approval you cannot give.** Before promising a fork run will go green, check
`gh api repos/O/R --jq .permissions`. If the active account has only `pull`, every `action_required`
run needs a maintainer click; say so once with the run IDs, stop retrying, and record it.

**"Review completed with incomplete coverage"** is a completed verdict: 0 findings, 0 threads, accept it
and carry the coverage caveat into the report. It is not the same state as `skipped`/`rate limited`,
which carry no verdict at all.

## 2. Loop mechanics

Iterate with a fixed interval (5 minutes is right: long enough for CI and CodeRabbit, short enough
to react). Each iteration:

1. Refresh state (see `scripts/prstate.py`).
2. Conflicts, only when they block you: `git fetch origin main && git merge origin/main`, resolve
   preserving both intents, fix merge fallout, run the gate, push. Do not chase a dirty branch that
   has nothing else to do — it will go dirty again. Never rebase or force-push a branch others build on.
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
7. Wait with `scripts/wait_verdict.py` (outside the agent): it polls to a `final` verdict on the
   target head with a **20,000-iteration** cap (default 300 s apart). Rate-limited waits are
   **free time** — they do not consume iterations. An `in_progress`/`pending` answer at the cap is a
   timeout, never a pass.

Budgets exist only to stop runaway loops; when the user raises one, the loop continues. Never yield
because of budget or a request cap: leave a precise hand-off and keep the state recoverable.

**A stopped agent is the single biggest source of lost work**, and it always fails the same way: a
turn is cut between `edit` and `commit`, leaving verified fixes in a worktree — often a temp one
(`C:/tmp/wt-…`) that can vanish. Rules that stop it:
- Commit before any long reasoning, build or test run, not after. Small, frequent commits.
- After every push, `git status --short` must be empty; a non-empty tree means the work is not safe.
- On respawn, `git status` in each worktree **first**, before reading the review: uncommitted changes
  are one turn from being lost, and they are worth more than the next finding.
- Never keep a working tree inside a temp directory for long-lived work; keep it under the user's
  workspace and only `CARGO_TARGET_DIR` in temp.

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

## 3b. Human reviews are a work list, not a gate status

Reviewers here post **AI-assisted "first-pass review notes"** as a `COMMENTED` review body — security,
tests, API, UI, docs angles combined — ending with a verdict like "Needs changes" and a numbered list.
They are not approvals and not CodeRabbit; treat them as tasks:

- Read the review **in full** (`gh api repos/O/R/pulls/N/reviews`), then for **each** numbered point
  decide against the **current head**: satisfied (reply with the commit or `file:line` that proves it),
  missing (implement it, with a test where behavioural), or wrong/out of scope (say so with evidence).
- A point marked "not certain" or "please verify" still needs a verdict — it is not permission to skip.
- Then post `@<reviewer> ready` with the head SHA and a per-point status line. Reply to the *review*,
  not just the PR, so it threads under their notes.
- A review written against an older head is **stale but still unanswered**: verify each point against
  the head and answer it; do not assume the code changed.
- Track answered-ness mechanically: a human event with no author activity after it is unanswered.
  `prstate.py` lists `human_reviews`/`human_comments` but not answered-ness — scan with
  `human_scan.py`-style logic (reviews + conversation per PR, compare against the author's latest
  comment) before declaring a repo clean.

## 3c. Budgets, supervision, and cheap waiting

- A subagent has a **soft request budget** (~200 requests) and is force-stopped when it is exceeded.
  That is a hand-off trigger, not a finish line: never end with `review_state: in_progress`,
  `pending`, `none`, or `paused`. Respawn, or hand the wait to `scripts/wait_verdict.py`.
- **Do not poll inside an agent.** Polling burns the budget you need for fixes. Run
  `scripts/wait_verdict.py --repo OWNER/NAME --pr N` outside the agent (default **20,000 iterations**,
  300 s apart) and wake an agent only when the verdict lands or a check fails. The iteration cap is
  a runaway stop, not a target.
- The review-slot claim is **per organisation**: the gridcraft slot and the deckcraft slot are
  independent. Announce claims per repo; yield to the oldest un-reviewed head in that repo only.
- A model that rejects forced tool calls (`400 only "auto" is supported for tool_choice`) cannot run
  these agents — the harness forces tool choice. Switch the subagent model rather than retrying.

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

## 5b. Builds: cache them, and watch the disk

Several PRs in parallel means several `target/` trees; the reference run filled the volume to 100% and
a full disk corrupted cargo metadata in a way that reads like a code failure — and on a Windows box a
third-party endpoint scanner intermittently refuses to execute a freshly written build script
(`os error 32`/`5`, bash exit 127), which is not a code failure either. Before a build: `df -h`. Use `sccache` (`RUSTC_WRAPPER`) with `CARGO_INCREMENTAL=0` and `CARGO_PROFILE_DEV_DEBUG=0`
so a deleted `target/` does not cost a cold rebuild, one `target/` per worktree (never a shared
`CARGO_TARGET_DIR` across concurrent agents), and delete a worktree's `target/` once that PR is at its
stop condition. Details and the verified numbers: `references/build-cache-and-disk.md`.

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

## 6b. Stopping safely

An agent can be stopped at any moment (budget, model switch, a human interrupt). Leave the branch in a
state the next agent can resume without archaeology — the reference incident was a PR closed by its own
agent nine seconds before it recorded the fix, so the fix was never reviewed.

- **Never leave a half-applied edit.** Either finish the change and push it, or `git checkout -- .` and
  record what you discarded. A worktree that does not compile is worse than no worktree.
- **Unpushed commits are the same hazard**: a WIP commit in a worktree is invisible to everyone else.
  Push it (fast-forward, even if incomplete) or drop it, and say which in the log.
- **Write the hand-off before you stop** — head SHA, what is done, what is left, and the exact next
  command. The next agent re-reads it; it must not have to infer state from a diff.
- **Never stop at `in_progress`.** A review in flight is not a clean PR and not a blocked one: it is a
  wait. Leave the branch pushed and the waiter running (`scripts/wait_verdict.py`, up to 20,000
  iterations); the verdict or a CI failure wakes the next agent.
- **Confirm PR state after every write.** `gh api repos/O/R/pulls/N --jq .state` should read `open`.
  A CodeRabbit reply like "Pull request is closed" means you broke something, not that the bot is
  flaky.

## 7. Do not

- Do not close, merge, or reopen a PR — that is a maintainer action, and the author's token is usually
  read-only upstream anyway. The writes you own are: push to the fork branch, comment, reply in a review
  thread, resolve a thread, request a review, edit the body.
- Do not force-push a shared branch, or delete a test to make CI pass.
- Do not treat a green check run from a prerequisite's commit as your PR's review.
- Do not let a "stable" report from another agent end your loop; re-read the live state.
- Do not read images on a runtime that cannot accept image input; assert on artifacts in tests.
- Do not accept a hand-off that contains only prose. If a run produced no diff, no commit and no
  PR state change, it did not happen: re-run it with the method rule "read → edit → run the smallest
  proving test", not with a fresh plan.
