# Loop protocol (full text)

This is the operational version of the loop. It assumes `gh` is authenticated as the PR author and
that the author may be read-only on the upstream repository.

## Cadence and accounting

- Interval: 300 s between iterations.
- Budgets are safety caps, not goals. When the user raises the cap (2400 → 24000), the loop simply
  continues.
- An iteration that only waits on a speed-limited CodeRabbit does **not** count; time spent waiting
  on quota is free. Use it for the non-CR work: local gates, replay items, registry rows, CI logs.
- Never end a loop because of a model/request cap. Write the hand-off into the log and leave the
  branch in a state the next agent can pick up without archaeology.

## Per-iteration order

1. **State.** `python prstate.py <PR>` (with `PR_REPO=<owner>/<repo>` when not the default). Read
   `cr.actionable`, `cr.outside_diff`, `cr.additional`, `unresolved_threads`, `ci.failed`,
   `ci.pending`, `mergeable`.
2. **Conflicts first.** A dirty PR blocks everything else: `git fetch origin main && git merge
   origin/main`, resolve keeping both intents, fix fallout, run the gate, push fast-forward.
3. **CodeRabbit.** Fix findings; reply with the fixing commit; resolve the thread. Body-only
   findings (outside-diff, additional, unverified) have no thread — record the fix in a PR comment.
4. **CI.** `gh run view <run_id> --log-failed`; fix the cause; push.
5. **Humans.** Implement each review point; reply bullet-by-bullet; `@<login> ready`.
6. **CodeRabbit liveness.** If the head has no completed successful run and no run is in progress:
   post `@coderabbitai full review` (once per head SHA per quota window) and, when the bot reports
   paused, `@coderabbitai resume`. `@coderabbitai review` is ignored in the skipped/paused state.
7. **Sleep 300 s.**

## Commands

```bash
# state
python prstate.py 1234                     # default repo
PR_REPO=owner/repo python prstate.py 1234

# reply in a review thread (id from state output) then resolve it
gh api repos/OWNER/REPO/pulls/1234/comments/<comment_id>/replies -f body="Fixed in <sha>: ..."
gh api graphql -f query='mutation { resolveReviewThread(input:{threadId:"<id>"}) { thread { isResolved } } }'

# CodeRabbit nudges
gh pr comment 1234 --repo OWNER/REPO --body "@coderabbitai resume"
gh pr comment 1234 --repo OWNER/REPO --body "@coderabbitai full review"

# CI
gh pr checks 1234 --repo OWNER/REPO
gh run view <run_id> --repo OWNER/REPO --log-failed

# push (fork remote resolves to the author's fork)
git push <remote> HEAD:<branch>
```

## The repo's own gate (do not skip it)

Find the documented quality command in `AGENTS.md` / `CONTRIBUTING.md` / CI workflow — in the craft
repos it is `cargo xtask ci` (fmt, clippy `-D warnings`, tests, doctests, assets, brands, layers,
wasm, cleanroom). Run it **end to end** on the head you are about to push, not just the touched
crates. Record, per step: command, exit code, first failing output.

```bash
cargo xtask ci 2>&1 | tee /tmp/xtask-ci.log; echo "exit=$?"
# or step by step, same order, recording each:
cargo fmt --all -- --check; echo "fmt=$?"
cargo clippy --workspace --all-targets -- -D warnings; echo "clippy=$?"
cargo test --workspace --all-targets; echo "test=$?"
cargo xtask assets; cargo xtask brands; cargo xtask layers; cargo xtask wasm; cargo xtask cleanroom
```

If a step fails and it is not your change, prove it on upstream `main` (scratch worktree or stash,
same command) and report both outputs. Do not "fix" it in your PR and do not tick it.

PR checklists: `gh pr view <PR> --json body` then tick only what passed —

```bash
gh pr view 1234 --json body --jq .body > body.md   # edit "- [ ]" → "- [x]" for green steps only
gh pr edit 1234 --body-file body.md
```

Then reply **to the blocking review**, naming the reviewer, with commands, per-step results and the
head SHA. A general PR comment is not an answer to a `CHANGES_REQUESTED` review.

## Human review state is a gate

Every iteration, read it — not once at the start:

```bash
gh pr view 1234 --json reviewDecision,reviews \
  --jq '{decision: .reviewDecision, reviews: [.reviews[]|{a:.author.login,s:.state,at:.submittedAt,commit:(.commitId // "")}]}'
```

- `reviewDecision: CHANGES_REQUESTED` blocks regardless of CodeRabbit counts.
- An `APPROVED` from before your latest push is stale; treat the head as unreviewed.
- The reviewer's asks are the work list: implement, then answer bullet-by-bullet, then `@<login> ready`.
- `Status: waiting_author` inside a review body means exactly that: the author must act.

## Fork CI: absent is not green

```bash
gh pr checks 1234                       # may list only CodeRabbit
gh api repos/OWNER/REPO/commits/<sha>/check-suites --jq '.check_suites[]|"\(.app.slug) \(.status) \(.conclusion)"'
gh api repos/OWNER/REPO/actions/runs?head_sha=<sha> --jq '.workflow_runs[]|"\(.name) \(.status) \(.conclusion)"'
```

`action_required` on every workflow, an empty `/statuses` (`total_count: 0`), and a `pending`
combined status mean: no CI ran. Record it as absent, attach the local gate transcript, and expect a
maintainer to ask for exactly that.

## Wording for the PR body

State the stacked/limited situation explicitly so the maintainer does not read stale counts as open
work:

> Review status: 0 actionable findings in files this PR owns. The `actionable: 3` in CodeRabbit's last
> body refers to prerequisite-owned files (`crates/dax/...`), answered with the owning PR's commits
> (`<sha>`), and its threads are resolved. Hosted workflows on this fork require maintainer approval;
> local gate: `<commands and results>`.

## Failure modes seen in practice

| Symptom | Cause | Fix |
|---|---|---|
| `actionable: 0` but a finding is still open | newest review is an empty stub | aggregate counts per head, not per newest review |
| Thread resolved, count unchanged | count is per review body, not state | record it as stale in the PR body and log |
| CodeRabbit never re-reviews | skipped/paused/rate limited | `@coderabbitai resume` + `@coderabbitai full review`, one per head per window |
| Two PRs fix the same defect differently | duplicated code across stacks | findings registry: claim before fixing |
| CI red only on Windows | env lint, font glyphs, CRLF parity check | prove it pre-existing on `main`, record, rely on the Linux gate |
| Every split PR rate-limited at once | shared OSS review pool | one outstanding request, dependency order |
