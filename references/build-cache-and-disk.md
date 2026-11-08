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
   serialises on the lock and turns parallel work into a queue.
3. Delete a worktree's `target/` as soon as that PR is at its stop condition; keep the worktree for
   git operations.
4. `CARGO_PROFILE_DEV_DEBUG=0` and `CARGO_INCREMENTAL=0` in agent environments: smaller artifacts,
   fewer rebuilds.
5. If a build fails with corrupt metadata, delete that `target/` and rebuild — do not chase it as a
   code bug.
6. Check `git status` before assuming a failed build means a bad edit: a half-written target can fail
   any crate.
