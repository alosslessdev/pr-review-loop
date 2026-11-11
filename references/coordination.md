# Coordination: one owner per defect

Several PRs that share code will attract the same CodeRabbit finding. Duplicate code is acceptable;
duplicate *fixes* are not — they diverge, and the maintainer then has two incompatible answers to one
defect.

## Findings registry

Append-only file, one row per cross-slice finding. Create it at the start of a multi-PR loop.

```
| PR | file:line | finding (one line) | owner PR | status | commit |
| 3412 | crates/dax/src/value.rs:146 | Currency text via f64 loses digits above ~9e11 | 3406 | fixed | 415c715b |
```

Protocol:

1. Before fixing a finding in a file you do not own: `grep -i "<file>" findings-registry.md`.
2. Already claimed → reply with the owning PR and commit, resolve your thread, stop.
3. Not claimed → append a row with yourself as owner **before** you push, then update your row with
   the commit. Never rewrite another agent's row.
4. Genuinely duplicated copies (two files with the same helper): the first claimant picks the
   approach, the rest mirror it and name the row.

Append safely from several agents:

```bash
printf '%s\n' "| $PR | $FILE:$LINE | $FINDING | $OWNER | claimed | - |" >> findings-registry.md
```

## Direct messaging beats the registry

When agents can message each other, ask before duplicating:

> "Is `crates/dax/src/value.rs` Currency `to_text` yours (#3406)? I have the same finding on #3412."

An owner that fixes it posts the commit; everyone else points.

## Shared review quotas

Managed CodeRabbit installations share an OSS review pool. Ten PRs requesting `full review` in
parallel starve each other. Queue them:

- One outstanding `@coderabbitai full review` across the whole set — and **announce the claim**
  (`write agent://all` "PR NNNN has claimed the review slot") so siblings do not queue behind a stale
  assumption. Release it explicitly when the review lands or the request is refused.
- Order by dependency (prerequisite first), then oldest un-reviewed head.
- Before requesting, check the other PRs' CodeRabbit check runs; skip your turn if one is in
  progress.
- A head whose only CR activity is `review rate limited` is not a failure: record it and keep the
  local gate green.

### Slots are per repository, and collisions are normal

The quota is shared per **organisation**, not across every repo you are working: a `storytold/gridcraft`
holder does not block a `storytold/deckcraft` request. Announce claims as `repo #N head` so nobody has to
infer it.

Simultaneous claims happen whenever four agents wake on the same broadcast — this session produced three
collisions in an hour. Resolve them deterministically, without a second round of negotiation:

- **First claim wins.** Compare the announcement order (the message id is monotonic), not the head date.
  A later claimer releases and waits, even if its head is older.
- **The parent arbitrates and broadcasts a FIFO list** naming each pending claim, e.g.
  `A #3556 → B #3413 → C #3599 → D #3412`. Siblings then take the slot in that order and stop claiming.
- **A refusal is not a holder.** `Review rate limited` comes from the service; it tells you the pool is
  empty, not that another agent holds the slot. Do not repost in a loop — wait for the window (about
  45 minutes) or for the holder's release, then post **once**.
- An agent that claims, does its fixes first, and only then requests wastes no quota; posting the request
  early and fixing while it runs means the verdict covers the pre-fix head and you pay twice.

## Fan-out

Parallel subagents are worth it when findings partition by file. Keep at every depth:

- Exactly one integration owner per branch: only it commits, pushes, replies and resolves.
- Disjoint files per worker; each worker gets the finding text, the acceptance test and the repo
  rules.
- Workers may run `cargo check`/`cargo test -p <crate>`; the build lock serialises them, which is
  fine.
- Do not split work that needs one coherent design across files — give one worker the whole slice.

## Hand-offs

Every agent writes a per-PR log: head SHA, commits with one-line rationale, findings fixed, gate
results, what remains. A hand-off that says "done" is a claim; the next agent re-reads the live state
before trusting it.
