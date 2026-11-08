Builds, disk and multiple worktrees

A PR loop runs many branches at once, each with its own `target/`. On one machine that fills the disk:
the reference run hit **100%** with nine agents building, and a full disk corrupts cargo metadata
(`failed to mmap libserde-*.rmeta`, `staticlib std instead of rlib`) — errors that look like code
failures and are not.

## sccache

Compiled objects are cacheable; linked artifacts are not. With sccache you can delete a `target/`
without paying the full compile cost again, which is what makes "clean up the disk between PRs"
practical.

```bash
# install (Windows: prebuilt release asset is fastest)
gh release download v0.19.0 --repo mozilla/sccache \
  --pattern "sccache-v0.19.0-x86_64-pc-windows-msvc.zip" --output sccache.zip
unzip -o sccache.zip && cp sccache-v0.19.0-x86_64-pc-windows-msvc/sccache.exe ~/.local/bin/

# per shell / per agent
export RUSTC_WRAPPER="$HOME/.local/bin/sccache"
export SCCACHE_DIR="$HOME/sccache"
export SCCACHE_CACHE_SIZE=20G
export CARGO_INCREMENTAL=0            # incremental + sccache is wasted disk and defeats caching
export CARGO_PROFILE_DEV_DEBUG=0      # dev debuginfo is the single biggest target/ consumer
sccache --start-server
sccache --show-stats                  # watch "Cache hits (Rust)"
```

Verified behaviour: a `cargo check` of one crate after `cargo clean -p <crate>` hit the cache and
finished in ~34 s where the cold build of the same crate and its deps took ~8.5 min.

Honest limits:
- **Hits are per exact rustc invocation.** A different worktree path changes the arguments, so
  cross-worktree hits are unlikely unless the build remaps paths. The wins are: cleaning a `target/`
  and rebuilding, switching branches in the same worktree, and re-running after a failed gate.
- **Linking is not cached.** Each worktree still needs room for its final artifacts; sccache removes
  the *recompilation* cost, not the linking cost.
- The cache itself costs disk (`SCCACHE_CACHE_SIZE`); keep it on the same volume and evict by size.

## Disk hygiene for a loop

1. `df -h` before starting a build; stop and clean if free space is under ~10% of the volume.
2. One `target/` per worktree, never a shared `CARGO_TARGET_DIR` across concurrent agents — cargo
   serialises on the lock and turns parallel work into a queue. **And never share one across
   branches:** rlibs of workspace crates are keyed by path, so a worktree that reuses another branch's
   target dir links *that* branch's `gridcraft-chart`/`-engine` and you get phantom failures *and*
   phantom passes. Reference case: a merged tree "failed" a ribbon assertion because the engine rlib
   came from a sibling branch; `cargo clean -p <every workspace crate>` plus a rebuild from the tree
   under test made it pass, with no source change. Sharing is only safe for registry dependencies.
3. Delete a worktree's `target/` as soon as that PR is at its stop condition; keep the worktree for
   git operations.
4. `CARGO_PROFILE_DEV_DEBUG=0` and `CARGO_INCREMENTAL=0` in agent environments: smaller artifacts,
   fewer rebuilds.
5. If a build fails with corrupt metadata, delete that `target/` and rebuild — do not chase it as a
   code bug.
6. **A full volume can damage a worktree, not just a build.** Observed at 568 MB free: `.git` plus
   2 532 tracked files vanished from the worktree, and a test failed with `could not execute process …
   The system cannot find the file specified`. Recovery: `git checkout -f HEAD -- .` restores every
   tracked file from the object store; the branch is fine. Check `git status` before believing a file
   is gone.
7. When several agents build at once, a test that grants a spawned process a fixed timeout (30 s
   startup checks, timing assertions) can fail purely from load. Re-run it serially before treating it
   as a code failure — the reference run had 18 concurrent cargo builds.
6. Check `git status` before assuming a failed build means a bad edit: a half-written target can fail
   any crate.


## Freshly built executables that will not run (Windows endpoints)

Observed on a Windows box with a third-party endpoint scanner:

```
error: could not execute process `.../build/serde_core-*/build-script-build` (never executed)
Caused by: ... (os error 32)   # or (os error 5)
```

and, in a bash shell, `command not found` (exit 127) when launching any *newly written* executable,
while PowerShell runs the same file fine. The binary itself is intact — PowerShell reports its size and
executes it.

Diagnosis: the scanner inspects freshly written PE files and briefly denies read/execute; bash in this
harness surfaces that as 127, cargo as os error 32/5. On the reference box the holder was identified
precisely — **Bitdefender Endpoint Security Service (`EPSecurityService`)**, confirmed with Restart
Manager (`Get-Process -Id … | Select-Object -ExpandProperty Modules`, or `handle.exe`), locking freshly
linked `serde`/`serde_core` build-script images across *every* fresh target dir (`target/`, `t2/`,
`t3/`). It is not the disk, not `target/`, and not
sccache: `cargo clean`, a fresh target dir and a retry of the same command do not always clear it.

What actually works, cheapest first:

1. **Retry the build.** The denial is usually transient — the same target dir builds moments later.
2. **Run manual executables through PowerShell**, not bash: `powershell -NoProfile -Command "& '.\path\to.exe'"`.
3. **Change the build-script binary's content** so it is not the blocked file:
   `export RUSTFLAGS="-C opt-level=1"`. This changes every rustc invocation, so it **resets the sccache
   keys** — pick one RUSTFLAGS value for the machine and keep it; toggling per build gives you a cold
   cache every time.
   `CARGO_PROFILE_DEV_BUILD_OVERRIDE_DEBUG/OPT_LEVEL` does **not** help on MSVC: debuginfo lives in the
   `.pdb`, so the `.exe` stays byte-identical.
4. **Seed the target dir from an already-scanned build of the same commit** — artifacts that a scan has
   already released keep working, so copying `target/` from a sibling worktree at the same head lets
   the gate run. This is what unblocked the reference box's `dax` gate while `cargo xtask ci` still
   could not link fresh build scripts.
5. Retrying with a *different* crate order or `-j 1` sometimes slips past the lock window; both are
   cheaper than a flag change.
6. Adding an antivirus exclusion would be the clean fix but needs admin (`Add-MpPreference` fails with
   "You don't have enough permissions"; `Get-MpPreference` can fail with `0x800106ba` when Defender is
   disabled in favour of a third-party product such as Bitdefender). Escalate to the machine owner —
   this is not something the loop can fix.

Note this interacts with parallel agents: several builds writing executables at once raise the chance
of a scan collision, so `-j 4` (or lower) is steadier than a full-core build.


## When the local gate cannot run at all

Some environments make `cargo xtask ci` (or any workspace-wide clippy/test) impossible: the endpoint
scanner holds freshly linked build scripts, `link.exe` fails with 1104, and the step dies before our
crates compile. Four retries, `-j 2`, RUSTFLAGS changes and sccache do not fix it.

Do not fake it and do not block the PR on it. Instead:

1. Run the **touched-crate** gate locally and keep it green: `cargo fmt --all`,
   `cargo clippy -p <crates> --all-targets -- -D warnings`, `cargo test -p <crates>`.
2. Rely on the **hosted `gate` job** as the authoritative full run — on a fork PR it needs maintainer
   approval (`action_required`); say so and point at the workflow run rather than claiming CI.
3. State the situation in the PR body's verification block: which steps ran locally, with exit codes,
   and that the full `cargo xtask ci` is covered by the hosted `gate` job, naming the environment
   blocker (not "could not run").
4. Record it in the loop log so the next agent does not burn an hour rediscovering it.

A blocker you can name with evidence is honest; a partial run reported as "green" is not.
