# Splitting a monolithic PR into reviewable slices

Written from a real split of a 180-file / +74k-line PR into ten PRs. The numbers below are what made
the split defensible to a maintainer, so measure your own repo first.

## 0. Measure the ceiling

Take the last ~800 merged PRs (`state=closed`, filter `merged_at`) and get `additions + deletions` and
`changed_files` per PR (GraphQL: `additions deletions changedFiles`). In the reference repo:

| Repo | median lines | largest feature merge | largest overall |
|---|---|---|---|
| Rust spreadsheet app | 75 | ~30k (247 files, "backlog integration") | 95k (mechanical module-layout refactor) |
| Rust presentation app | 103 | ~11.5k | 87k (same refactor) |
| Rust document app | 84 | ~65k (engine refactor) | 246k (same refactor) |

Rule of thumb: keep every slice **≤ the largest feature merge**, well below the mechanical-refactor
outliers. Size slices against that, not against taste.

## 1. Find the real dependency graph

- New crate? Then exactly one PR creates it: workspace `Cargo.toml`, `Cargo.lock` stanza, crate
  `Cargo.toml`, `lib.rs` module tree. Every other PR must show **no crate-creation hunk** — this is
  the specific objection maintainers raise on stacked series ("each PR repeats the crate creation").
- Dispatch registries (function-pointer arrays, command tables) are the second hazard: if every slice
  inserts at the same anchor, every pair conflicts. Fix by construction: the prerequisite ships one
  inert comment marker per slice, and each slice replaces **only its own** line.
- Prefer one prerequisite + leaves. Depth-1 fan-out means out-of-order merges cost a red check, not
  a conflict. A deep stack serialises everything and re-creates the rebase treadmill.

## 2. Cut the branches

For each slice, from `origin/main`:

```bash
git switch -c <slice> origin/main
git checkout <monolith-sha> -- <owned paths>      # only the files this slice owns
# hand-apply only the registration lines it needs (never the whole shared file)
cargo fmt --all && cargo clippy -p <crates> --all-targets -- -D warnings && cargo test -p <crates>
```

Hand-applying registration lines matters: taking a shared file wholesale imports slice-specific code
from other slices, which is what creates duplicate fixes later.

## 3. Prove three invariants before opening anything

1. **Own-file disjointness.**
   ```bash
   own() { git log --no-merges --name-only --pretty=format: upstream/main..fork/"$1"; }
   comm -12 <(own slice-a | sort -u) <(own slice-b | sort -u)   # must be empty for leaf pairs
   ```
   Note the trap: `git log <base>..<branch>` also lists commits merged from `main`, so compute each
   branch's own files relative to `upstream/main` and subtract the prerequisite's own files.
2. **Coverage equality.** The union of the slices' changed files must **equal** the monolith's file
   set. Compute it from the API (`/pulls/<n>/files`, paginated) — a 180 vs 179 difference is a lost
   file, and it is easy to miss.
3. **No crate-creation outside the prerequisite.**
   ```bash
   git diff --name-only <prereq> <slice> -- Cargo.toml Cargo.lock crates/<crate>/Cargo.toml
   ```

## 4. The replay problem

Slices fork from a moving monolith. Any fix landed on the monolith after the fork point must be
replayed into the slice that owns those files.

- Do **not** assume cherry-picks work: if the prerequisite branch is not an ancestor of the monolith
  (they diverged at the upstream base), no monolith SHA is cherry-pickable.
- Port by hand, hunk by hunk, re-validating against the slice's own code; the branches diverged
  independently, so a hunk may not apply or may assume code the slice lacks.
- Where a "replay item" turns out to be a **semantic disagreement** (e.g. the monolith compares exact
  keys while the slice folds case), treat it as a design decision on the owning slice and write the
  decision into the module docs — do not copy expectations that its code cannot satisfy.
- Keep the monolith frozen at a recorded SHA as the reference, and close it only after coverage
  equality holds; put the split map in the closing comment and on the tracking issue.

## 5. What to say in each PR body

- A mermaid dependency graph, with the prerequisite arrows solid and no-compile-dependency arrows
  dotted.
- A table: branch, PR, prerequisite(s), what it shows.
- One line: only the prerequisite creates the crate; this PR shows only its own change.
- One line: merging out of order costs a red check, never a conflict.
- The stacked-head gate wording from `loop-protocol.md`, so stale counts are not read as open work.

## 6. When the split is not worth it

If the monolith is under the repo's ceiling, or the slices would each need the others' unmerged code
to compile *and* to satisfy their tests, keep it whole and spend the effort on review instead. A
split that produces nine PRs nobody can merge independently is worse than one large PR.
