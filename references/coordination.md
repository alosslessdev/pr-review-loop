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

- One outstanding `@coderabbitai full review` across the whole set.
- Order by dependency (prerequisite first), then oldest un-reviewed head.
- Before requesting, check the other PRs' CodeRabbit check runs; skip your turn if one is in
  progress.
- A head whose only CR activity is `review rate limited` is not a failure: record it and keep the
  local gate green.

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
