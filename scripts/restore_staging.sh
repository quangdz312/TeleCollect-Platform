#!/usr/bin/env bash
# Restore for the CPU-only staging stack. Always restores into a fresh
# TARGET directory you name explicitly — never into DATA_DIR, and never over
# a running stack's data. See docs/BACKUP_RESTORE.md for the safe swap-over
# steps after you have inspected the restored copy.
#
# Usage:
#   ./scripts/restore_staging.sh \
#     --sqlite-backup /srv/telecollect/backups/sqlite/app-20260823-091500.db \
#     --target /srv/telecollect/restore-test/20260823 \
#     [--artifacts-source /srv/telecollect/backups/artifacts]
#
#   ./scripts/restore_staging.sh --archive /srv/telecollect/backups/archives/telecollect-full-20260823-091500.tar.gz --target /srv/telecollect/restore-test/20260823
#
# Requires: sqlite3, sha256sum, tar, flock (util-linux — preinstalled on
# Debian/Ubuntu).

set -euo pipefail

# Everything this script writes under --target should be owner-only —
# app.db carries password hashes. See the final `chmod -R go-rwx` pass below
# for the actual guarantee (umask alone doesn't cover `cp -a`, which sets
# permissions explicitly from the source rather than through umask-affected
# creation calls); this covers the files created directly by this script.
umask 077

log() { printf '[restore] %s\n' "$*" >&2; }
die() { printf '[restore] ERROR: %s\n' "$*" >&2; exit 1; }

SQLITE_BACKUP=""
ARCHIVE=""
ARTIFACTS_SOURCE=""
TARGET=""

while [ $# -gt 0 ]; do
  case "$1" in
    --sqlite-backup) SQLITE_BACKUP="$2"; shift 2 ;;
    --archive) ARCHIVE="$2"; shift 2 ;;
    --artifacts-source) ARTIFACTS_SOURCE="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -n "$TARGET" ] || die "--target is required — an explicit, empty-or-nonexistent directory to restore into. This is never DATA_DIR."
if [ -n "$ARCHIVE" ] && [ -n "$SQLITE_BACKUP" ]; then
  die "pass either --archive or --sqlite-backup, not both"
fi
if [ -z "$ARCHIVE" ] && [ -z "$SQLITE_BACKUP" ]; then
  die "one of --archive or --sqlite-backup is required"
fi

for tool in sqlite3 sha256sum tar flock; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool '$tool' not found on PATH"
done

# ---- concurrency: best-effort shared lock against a concurrent backup ----
# A restore only reads from BACKUP_ROOT (it never writes there), so two
# restores running at once are not a correctness problem and are allowed to
# share this lock. What must not happen is reading files while
# backup_staging.sh's exclusive lock is held mid-write (e.g. retention
# deleting the very snapshot being restored, or the manifest being
# regenerated underneath a partial read).
#
# The lock file lives next to whichever backup resource is being read
# (backup_staging.sh always creates BACKUP_ROOT/.backup.lock there). If the
# resource came from somewhere else — copied to another machine, downloaded
# without its lock file — there is nothing to lock against and this is
# skipped rather than failing a legitimate restore.
try_shared_lock() {
  local resource_dir="$1"
  local lock_file="$resource_dir/.backup.lock"
  [ -f "$lock_file" ] || return 0
  exec 8>"$lock_file"
  flock -s -n 8 || die "backup_staging.sh appears to be running against '$resource_dir' right now (lock: $lock_file) — retry once it finishes"
  log "acquired shared lock against concurrent backup: $lock_file"
}

if [ -n "$SQLITE_BACKUP" ]; then
  try_shared_lock "$(cd "$(dirname "$SQLITE_BACKUP")" 2>/dev/null && pwd)"
fi
if [ -n "$ARCHIVE" ]; then
  try_shared_lock "$(cd "$(dirname "$ARCHIVE")" 2>/dev/null && pwd)"
fi
if [ -n "$ARTIFACTS_SOURCE" ] && [ -d "$ARTIFACTS_SOURCE" ]; then
  # Artifacts source is BACKUP_ROOT/artifacts — the lock lives one level up.
  try_shared_lock "$(cd "$ARTIFACTS_SOURCE/.." 2>/dev/null && pwd)"
fi

# ---- canonicalize --target before creating it ----
# Resolves to an absolute path whether or not TARGET exists yet: if it
# already exists, `cd`+`pwd` gives the canonical path directly (this also
# correctly handles "." — resolves to cwd, not cwd/."); if it doesn't exist,
# only its PARENT is created here (scaffolding, not the target itself — an
# empty directory node carries no data to race over) so the parent can be
# canonicalized the same way, and TARGET's basename is appended. TARGET
# itself is only created later, after the lock below and the recheck that
# depends on it.
if [ -e "$TARGET" ]; then
  [ -d "$TARGET" ] || die "--target '$TARGET' exists and is not a directory"
  TARGET_ABS="$(cd "$TARGET" && pwd)"
else
  TARGET_PARENT_RAW="$(dirname -- "$TARGET")"
  mkdir -p "$TARGET_PARENT_RAW" || die "cannot create parent directory of --target: $TARGET_PARENT_RAW"
  TARGET_ABS="$(cd "$TARGET_PARENT_RAW" && pwd)/$(basename -- "$TARGET")"
fi
[ "$TARGET_ABS" != "/" ] || die "--target resolved to '/' — refusing"

# ---- concurrency: exclusive lock on this specific target ----
# The shared lock above only protects the READ side (BACKUP_ROOT) against a
# concurrent backup. It does nothing to stop two restores that both target
# the same directory from racing each other: both could pass the
# empty-target check below before either has written anything, then both
# proceed to write — the exact race this lock closes.
#
# Lives in TARGET's PARENT, not inside TARGET itself: never appears in
# `find "$TARGET_ABS" -mindepth 1 -maxdepth 1` (the emptiness check right
# below), so it can never be mistaken for restored data or copied anywhere,
# and never has to be created/removed relative to a target directory this
# script doesn't own yet.
TARGET_PARENT="$(dirname -- "$TARGET_ABS")"
TARGET_NAME="$(basename -- "$TARGET_ABS")"
TARGET_LOCK="$TARGET_PARENT/.${TARGET_NAME}.restore.lock"
exec 7>"$TARGET_LOCK"
chmod 600 "$TARGET_LOCK" 2>/dev/null || true
flock -x -n 7 || die "another restore_staging.sh is already writing to target '$TARGET_ABS' (lock: $TARGET_LOCK) — refusing to run concurrently"
log "acquired exclusive lock on target: $TARGET_LOCK"
# fd 7 stays open (and the lock held) for the rest of the script; the kernel
# releases it automatically when this process exits, cleanly or not — no
# explicit unlock, and the lock file itself is never unlinked while held
# (unlinking a held lock risks a second process locking a stale inode while
# a new file of the same name is created underneath it, defeating the lock
# entirely). Leaving the empty file behind after a successful run is
# expected: flock's exclusivity comes from the lock being held, not from
# the file's mere existence, so a leftover file never blocks a later run.

# ---- refuse to restore over a live data directory ----
# Re-checked only now, AFTER the exclusive lock above is held: checking
# before the lock would leave a window where two concurrent restores both
# observe "target is empty" and both proceed to write. A restore target
# that happens to already hold an app.db almost certainly means "the
# running stack's data dir", which this script must never overwrite
# implicitly.
if [ -e "$TARGET_ABS/app.db" ]; then
  die "$TARGET_ABS already contains app.db — refusing to restore over existing data. Pick an empty --target."
fi
if [ -d "$TARGET_ABS" ] && [ -n "$(find "$TARGET_ABS" -mindepth 1 -maxdepth 1 2>/dev/null)" ]; then
  die "$TARGET_ABS is not empty — refusing to restore into a non-empty directory. Pick an empty or new --target."
fi

mkdir -p "$TARGET_ABS"
log "restoring into: $TARGET_ABS (temporary — this is NOT the live data directory)"

restore_from_sqlite_backup() {
  local src="$1"
  [ -f "$src" ] || die "sqlite backup not found: $src"
  [ -f "$src.sha256" ] || die "checksum file not found: $src.sha256 (refusing to restore an unverified backup)"

  log "verifying checksum: $src.sha256"
  ( cd "$(dirname "$src")" && sha256sum -c "$(basename "$src").sha256" ) \
    || die "checksum mismatch for $src — backup file may be corrupted or tampered with"

  cp "$src" "$TARGET_ABS/app.db"
  log "sqlite restored: $TARGET_ABS/app.db"

  # Open the restored copy read-only and run a real integrity check — a
  # checksum match only proves the bytes are the ones that were written at
  # backup time, not that they form a valid SQLite file.
  local check
  check="$(sqlite3 "file:$TARGET_ABS/app.db?mode=ro" "PRAGMA integrity_check;" 2>&1)" \
    || die "restored database failed to open: $check"
  if [ "$check" != "ok" ]; then
    die "restored database failed PRAGMA integrity_check: $check"
  fi
  log "integrity check OK"
}

# Verifies every file in an artifact mirror against its manifest.sha256
# BEFORE copying anything — a modified or missing artifact must stop the
# restore, not get copied and discovered later. Uses the same "cd into the
# directory, verify by relative path" pattern as the sqlite checksum check
# above, for the same reason: a checksum record must be validated against
# the file actually sitting at that path right now, never resolved through
# a stale absolute path pointing somewhere else.
verify_artifacts_manifest() {
  local source_dir="$1"
  local manifest="$source_dir/manifest.sha256"
  [ -f "$manifest" ] || die "no manifest.sha256 found in '$source_dir' — refusing to restore artifacts without integrity verification. This backup predates manifest support or is incomplete."
  log "verifying artifact manifest: $manifest"
  if [ ! -s "$manifest" ]; then
    # An empty manifest is only legitimate when the mirror genuinely has no
    # files — backup_staging.sh line 203 produces exactly this via
    # `xargs --no-run-if-empty` when `find` matches nothing. Count only
    # inside the four artifact subdirs (episodes/datasets/training/review) —
    # the same list the restore loops below actually copy from — rather than
    # every file under source_dir: in the --archive path, source_dir is
    # $EXTRACT_DIR, which also holds the extracted app-*.db and its .sha256
    # sidecar sitting alongside the artifact tree. Those are not covered by
    # this manifest and must not count as "artifacts the manifest missed".
    local real_file_count=0
    for sub in episodes datasets training review; do
      [ -d "$source_dir/$sub" ] || continue
      real_file_count=$((real_file_count + $(find "$source_dir/$sub" -type f | wc -l)))
    done
    if [ "$real_file_count" -eq 0 ]; then
      log "artifact manifest is empty and '$source_dir' has no artifact files — nothing to verify, treating as OK"
      return 0
    fi
    die "artifact manifest '$manifest' is empty but '$source_dir' contains $real_file_count file(s) — manifest looks truncated or incomplete. Refusing to restore."
  fi
  ( cd "$source_dir" && sha256sum -c manifest.sha256 ) \
    || die "artifact manifest verification failed for '$source_dir' — one or more files are modified, missing, or corrupted. Refusing to restore. See the manifest output above for which file(s)."
  log "artifact manifest OK"
}

if [ -n "$ARCHIVE" ]; then
  [ -f "$ARCHIVE" ] || die "archive not found: $ARCHIVE"
  [ -f "$ARCHIVE.sha256" ] || die "checksum file not found: $ARCHIVE.sha256"

  log "verifying archive checksum: $ARCHIVE.sha256"
  ( cd "$(dirname "$ARCHIVE")" && sha256sum -c "$(basename "$ARCHIVE").sha256" ) \
    || die "checksum mismatch for $ARCHIVE — archive may be corrupted or tampered with"

  EXTRACT_DIR="$TARGET_ABS/.archive-extract"
  mkdir -p "$EXTRACT_DIR"
  # Always clean up the scratch extraction dir, success or failure (e.g. a
  # manifest verification `die` below) — it's working space, not restored
  # data, and must not linger in $TARGET.
  trap 'rm -rf "$EXTRACT_DIR" 2>/dev/null' EXIT
  tar -xzf "$ARCHIVE" -C "$EXTRACT_DIR"

  FOUND_DB="$(find "$EXTRACT_DIR" -maxdepth 1 -name 'app-*.db' | head -n1)"
  [ -n "$FOUND_DB" ] || die "archive did not contain an app-*.db file"

  # The archive's manifest.sha256 (written by backup_staging.sh alongside
  # the artifact tree inside the tarball) covers the artifacts it contains —
  # verify before restoring them, same as the --artifacts-source path.
  if [ -f "$EXTRACT_DIR/manifest.sha256" ]; then
    verify_artifacts_manifest "$EXTRACT_DIR"
  else
    log "no manifest.sha256 in archive — skipping artifact integrity check (archive predates manifest support)"
  fi

  restore_from_sqlite_backup "$FOUND_DB"

  for sub in episodes datasets training review; do
    if [ -d "$EXTRACT_DIR/$sub" ]; then
      mkdir -p "$TARGET_ABS/$sub"
      cp -a "$EXTRACT_DIR/$sub/." "$TARGET_ABS/$sub/"
      log "restored artifacts: $sub"
    fi
  done
  rm -rf "$EXTRACT_DIR"
else
  # Verify artifacts BEFORE the sqlite restore, not after: "verify before
  # copying anything" means anything, not just the artifact files — a
  # manifest failure must leave $TARGET completely empty, not with a
  # restored app.db and no artifacts.
  if [ -n "$ARTIFACTS_SOURCE" ]; then
    [ -d "$ARTIFACTS_SOURCE" ] || die "artifacts source not found: $ARTIFACTS_SOURCE"
    verify_artifacts_manifest "$ARTIFACTS_SOURCE"
  fi

  restore_from_sqlite_backup "$SQLITE_BACKUP"

  if [ -n "$ARTIFACTS_SOURCE" ]; then
    for sub in episodes datasets training review; do
      [ -d "$ARTIFACTS_SOURCE/$sub" ] || continue
      mkdir -p "$TARGET_ABS/$sub"
      cp -a "$ARTIFACTS_SOURCE/$sub/." "$TARGET_ABS/$sub/"
      log "restored artifacts: $sub"
    done
  else
    log "no --artifacts-source given — only the database was restored"
  fi
fi

# ---- final permission sweep ----
# Belt and suspenders on top of umask: `cp -a`/`tar -x` preserve whatever
# mode the source had rather than going through umask-affected creation
# calls, so a world-readable source (or a source from before this hardening
# existed) could otherwise leave the restored copy world-readable too.
chmod 700 "$TARGET_ABS"
chmod -R go-rwx "$TARGET_ABS"

cat >&2 <<EOF

[restore] Done. Nothing in the live stack was touched.
[restore] Restored copy: $TARGET_ABS

[restore] To inspect without risk, point a throwaway backend at this copy, e.g.:
[restore]   docker run --rm -p 18000:8000 \\
[restore]     -e DATABASE_URL="sqlite+aiosqlite:////app/data/app.db" \\
[restore]     -e STORAGE_DIR=/app/data -e REVIEW_DIR=/app/data/review \\
[restore]     -e JWT_SECRET=restore-inspection-only \\
[restore]     -v "$TARGET_ABS:/app/data" <backend image>

[restore] See docs/BACKUP_RESTORE.md for the manual, deliberate steps to
[restore] promote this restored copy to the live data directory. This
[restore] script never does that automatically.
EOF
