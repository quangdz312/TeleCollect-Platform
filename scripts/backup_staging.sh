#!/usr/bin/env bash
# Backup for the CPU-only staging stack.
#
# Two things get backed up, both from the host bind mount (not from inside
# a container, and never a raw `cp` of a live SQLite file):
#   1. SQLite (app.db) — a consistent snapshot via `sqlite3 .backup`, timestamped,
#      with a sha256 checksum, kept for RETENTION_COUNT runs.
#   2. Artifacts (episodes/, datasets/, training/, review/) — an incremental
#      rsync mirror: unchanged files are skipped, nothing is deleted from the
#      destination on a normal run (see plan section 4.5: "dùng rclone copy
#      thay vì sync --delete", same reasoning applies here with rsync), plus
#      a sha256 manifest of the whole mirror so restore can verify it.
#
# `tmp/` (src/services/storage.py upload-validation scratch space) is never
# touched: this script only reads the four allowlisted subdirectories above.
# A secret accidentally placed INSIDE one of those four subdirectories (not
# just at the top level of DATA_DIR) is a real allowlist gap this script
# cannot fully close by directory name alone — see the suspicious-file scan
# below and docs/BACKUP_RESTORE.md for why secrets must live under
# /srv/telecollect/secrets instead.
#
# --full additionally writes a timestamped tar.gz of the sqlite backup +
# current artifact mirror into archives/, for the "before/start/after the
# grading window" milestones the plan calls out (section 5.3) — not meant to
# run daily, kept to ARCHIVE_RETENTION_COUNT copies.
#
# Usage:
#   ./scripts/backup_staging.sh              # sqlite snapshot + incremental artifact mirror
#   ./scripts/backup_staging.sh --full        # same, plus a full tar.gz archive
#
# Env overrides (all optional):
#   DATA_DIR               Source data directory (default: /srv/telecollect/data)
#   BACKUP_ROOT             Destination root, OUTSIDE DATA_DIR (default: /srv/telecollect/backups)
#   RETENTION_COUNT         SQLite snapshots to keep, integer >= 1 (default: 14)
#   ARCHIVE_RETENTION_COUNT Full archives to keep, --full only, integer >= 1 (default: 3)
#
# Requires: sqlite3, rsync, sha256sum, tar, flock (util-linux — preinstalled
# on Debian/Ubuntu).

set -euo pipefail

# Every file/dir this script creates should be owner-only. umask alone does
# NOT cover rsync's mirror (rsync sets explicit permissions matching the
# source via chmod, which ignores umask) or `cp -a`, so this is defense in
# depth — the actual guarantee comes from the `--chmod` on rsync below and
# the recursive `chmod` pass at the very end of the script. umask still
# matters for the plain files this script creates directly (checksums,
# manifest, sqlite/tar output via redirection).
umask 077

DATA_DIR="${DATA_DIR:-/srv/telecollect/data}"
BACKUP_ROOT="${BACKUP_ROOT:-/srv/telecollect/backups}"
RETENTION_COUNT="${RETENTION_COUNT:-14}"
ARCHIVE_RETENTION_COUNT="${ARCHIVE_RETENTION_COUNT:-3}"
FULL=0
if [ "${1:-}" = "--full" ]; then
  FULL=1
fi

log() { printf '[backup] %s\n' "$*" >&2; }
die() { printf '[backup] ERROR: %s\n' "$*" >&2; exit 1; }

# ---- fail fast: required tools ----
for tool in sqlite3 rsync sha256sum tar flock; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool '$tool' not found on PATH (Debian/Ubuntu: apt-get install -y sqlite3 rsync coreutils tar util-linux)"
done

# ---- fail fast: validate retention BEFORE any create/delete happens ----
# A bad value here must never reach the retention-delete loop below: -1
# would corrupt the loop bound after files are already gone, and a
# non-numeric value must not be silently ignored (bash arithmetic comparison
# on a non-integer is a runtime error, not a clean validation failure).
validate_positive_int() {
  local name="$1" value="$2"
  [[ "$value" =~ ^[0-9]+$ ]] || die "$name must be a positive integer, got: '$value'"
  [ "$value" -ge 1 ] || die "$name must be >= 1, got: '$value'"
}
validate_positive_int "RETENTION_COUNT" "$RETENTION_COUNT"
validate_positive_int "ARCHIVE_RETENTION_COUNT" "$ARCHIVE_RETENTION_COUNT"

# ---- fail fast: source layout ----
DB_PATH="${DB_PATH:-$DATA_DIR/app.db}"
[ -d "$DATA_DIR" ] || die "DATA_DIR '$DATA_DIR' does not exist"
[ -f "$DB_PATH" ] || die "DB_PATH '$DB_PATH' does not exist — is the backend running against this DATA_DIR?"

# Resolve to an absolute path so BACKUP_ROOT can never end up nested inside
# the very directory it is backing up (a sync-into-itself footgun).
DATA_DIR_ABS="$(cd "$DATA_DIR" && pwd)"

# ---- fail fast: destination writable, disk available ----
mkdir -p "$BACKUP_ROOT" \
  || die "cannot create BACKUP_ROOT '$BACKUP_ROOT' (permission or missing parent?)"
BACKUP_ROOT_ABS="$(cd "$BACKUP_ROOT" && pwd)"
case "$BACKUP_ROOT_ABS" in
  "$DATA_DIR_ABS"|"$DATA_DIR_ABS"/*)
    die "BACKUP_ROOT '$BACKUP_ROOT_ABS' is inside DATA_DIR '$DATA_DIR_ABS' — backups must live outside the data directory"
    ;;
esac

# ---- concurrency: refuse to run a second backup against the same root ----
# Held for the entire script (fd 9 stays open until the process exits), so
# every step below — retention deletes included — only ever runs under this
# lock. Non-blocking: a second invocation fails immediately and visibly
# instead of queuing silently or racing the first one on the same files.
LOCK_FILE="$BACKUP_ROOT_ABS/.backup.lock"
exec 9>"$LOCK_FILE"
flock -n 9 || die "another backup_staging.sh is already running against BACKUP_ROOT '$BACKUP_ROOT_ABS' (lock: $LOCK_FILE) — refusing to run concurrently"

mkdir -p "$BACKUP_ROOT/sqlite" "$BACKUP_ROOT/artifacts" "$BACKUP_ROOT/archives" \
  || die "cannot create directories under BACKUP_ROOT '$BACKUP_ROOT'"

touch "$BACKUP_ROOT/.write_test" 2>/dev/null || die "BACKUP_ROOT '$BACKUP_ROOT' is not writable"
rm -f "$BACKUP_ROOT/.write_test"

AVAILABLE_KB=$(df -Pk "$BACKUP_ROOT" | awk 'NR==2 {print $4}')
if [ -n "$AVAILABLE_KB" ] && [ "$AVAILABLE_KB" -lt 524288 ]; then
  die "less than 512MB free at BACKUP_ROOT ($((AVAILABLE_KB / 1024))MB) — refusing to start a backup that would fail partway through"
fi

TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"

# ---- 0. Secret defense-in-depth: refuse to back up suspicious files ----
# The real boundary is "secrets live under /srv/telecollect/secrets, never
# under DATA_DIR" (docs/BACKUP_RESTORE.md) — this scan is a second line of
# defense for the case someone drops a credential-shaped file *inside* one
# of the four allowlisted subdirectories (e.g. episodes/.env), which the
# directory-name allowlist alone would not catch.
SECRET_PATTERNS=(-name '.env' -o -name '.env.*' -o -name 'secrets' -o -name 'id_rsa' -o -name 'id_ed25519' -o -name '*.pem' -o -name '*.key')
for sub in episodes datasets training review; do
  SRC="$DATA_DIR/$sub"
  [ -d "$SRC" ] || continue
  mapfile -t SUSPECTS < <(find "$SRC" \( "${SECRET_PATTERNS[@]}" \) 2>/dev/null)
  if [ "${#SUSPECTS[@]}" -gt 0 ]; then
    die "refusing to back up: file(s) matching secret-like patterns found inside '$SRC': ${SUSPECTS[*]} — remove them or move them outside DATA_DIR (secrets belong under /srv/telecollect/secrets) and retry"
  fi
done

# ---- 1. SQLite consistent backup ----
# `.backup` drives SQLite's own online backup API (safe against concurrent
# writers) — this is not a file copy of app.db while it may be mid-write.
SQLITE_DEST="$BACKUP_ROOT/sqlite/app-$TIMESTAMP.db"
log "sqlite backup: $DB_PATH -> $SQLITE_DEST"
sqlite3 "$DB_PATH" ".backup '$SQLITE_DEST'" || die "sqlite3 .backup failed"
[ -s "$SQLITE_DEST" ] || die "sqlite backup produced an empty file"
# Record only the basename in the checksum file, not the absolute path:
# `sha256sum -c` trusts the filename field literally, so an absolute-path
# record would make restore_staging.sh's checksum check silently verify
# whatever still sits at that original path instead of the (possibly moved
# or copied elsewhere) file actually being restored — a corrupted copy could
# pass verification while the untouched original quietly "backs" it.
( cd "$(dirname "$SQLITE_DEST")" && sha256sum "$(basename "$SQLITE_DEST")" ) > "$SQLITE_DEST.sha256"
log "sqlite backup OK: $(awk '{print $1}' "$SQLITE_DEST.sha256")"

# Retention: keep the newest RETENTION_COUNT snapshots (name is
# timestamp-sortable), delete the rest along with their checksum files.
# RETENTION_COUNT was already validated above, so this arithmetic is safe.
mapfile -t SQLITE_BACKUPS < <(find "$BACKUP_ROOT/sqlite" -maxdepth 1 -name 'app-*.db' | sort)
COUNT=${#SQLITE_BACKUPS[@]}
if [ "$COUNT" -gt "$RETENTION_COUNT" ]; then
  TO_DELETE=$((COUNT - RETENTION_COUNT))
  for ((i = 0; i < TO_DELETE; i++)); do
    log "retention: removing old snapshot ${SQLITE_BACKUPS[$i]}"
    rm -f "${SQLITE_BACKUPS[$i]}" "${SQLITE_BACKUPS[$i]}.sha256"
  done
fi

# ---- 2. Artifacts: incremental mirror, never --delete ----
# Allowlist, not "everything under DATA_DIR": excludes tmp/ (ephemeral
# upload-validation scratch space) and anything else that might show up
# there later without needing to remember to exclude it explicitly.
#
# --chmod forces destination permissions explicitly instead of letting -a
# preserve whatever mode the source file happened to have — rsync sets
# permissions via chmod() on the destination, which is not subject to
# umask, so without this a world-readable source file would stay
# world-readable in the backup regardless of the umask set above.
for sub in episodes datasets training review; do
  SRC="$DATA_DIR/$sub"
  [ -d "$SRC" ] || { log "skip: $SRC does not exist yet"; continue; }
  DEST="$BACKUP_ROOT/artifacts/$sub"
  mkdir -p "$DEST"
  log "artifact mirror: $SRC -> $DEST"
  rsync -a --chmod=Du=rwx,Dgo=,Fu=rw,Fgo= "$SRC/" "$DEST/"
done

# Sha256 manifest of the whole artifact mirror, so restore_staging.sh can
# verify individual files instead of trusting the mirror blindly (rsync's
# own transfer checksums only protect against corruption in transit, not
# against the destination file being modified or deleted afterwards).
# Paths are relative (./episodes/...) so the manifest stays valid if the
# backup is copied to a different machine/directory.
#
# Written to a temp file first, then renamed into place: a `mv` within the
# same directory is atomic, so a backup that fails partway through (disk
# full, killed mid-run) never leaves behind a manifest that claims success
# for a mirror that isn't actually complete.
MANIFEST="$BACKUP_ROOT/artifacts/manifest.sha256"
# Temp file lives one level up (BACKUP_ROOT, not BACKUP_ROOT/artifacts) so
# `find .` below — rooted at artifacts/ — can never see it and hash it into
# its own manifest before the rename. `mv` stays atomic across the two
# since both are on the same filesystem (both under BACKUP_ROOT).
MANIFEST_TMP="$(mktemp "$BACKUP_ROOT/.manifest.sha256.XXXXXX")"
( cd "$BACKUP_ROOT/artifacts" && find . -type f ! -name 'manifest.sha256' -print0 | sort -z | xargs -0 --no-run-if-empty sha256sum ) > "$MANIFEST_TMP"
chmod 600 "$MANIFEST_TMP"
mv -f "$MANIFEST_TMP" "$MANIFEST"
log "artifact manifest: $MANIFEST ($(wc -l < "$MANIFEST") files)"

# ---- 3. Optional full archive ----
if [ "$FULL" -eq 1 ]; then
  ARCHIVE_DEST="$BACKUP_ROOT/archives/telecollect-full-$TIMESTAMP.tar.gz"
  log "full archive: $ARCHIVE_DEST"
  tar -czf "$ARCHIVE_DEST" \
    -C "$BACKUP_ROOT/sqlite" "$(basename "$SQLITE_DEST")" "$(basename "$SQLITE_DEST").sha256" \
    -C "$BACKUP_ROOT/artifacts" .
  ( cd "$(dirname "$ARCHIVE_DEST")" && sha256sum "$(basename "$ARCHIVE_DEST")" ) > "$ARCHIVE_DEST.sha256"
  log "full archive OK: $(awk '{print $1}' "$ARCHIVE_DEST.sha256")"

  mapfile -t ARCHIVES < <(find "$BACKUP_ROOT/archives" -maxdepth 1 -name 'telecollect-full-*.tar.gz' | sort)
  COUNT=${#ARCHIVES[@]}
  if [ "$COUNT" -gt "$ARCHIVE_RETENTION_COUNT" ]; then
    TO_DELETE=$((COUNT - ARCHIVE_RETENTION_COUNT))
    for ((i = 0; i < TO_DELETE; i++)); do
      log "retention: removing old archive ${ARCHIVES[$i]}"
      rm -f "${ARCHIVES[$i]}" "${ARCHIVES[$i]}.sha256"
    done
  fi
fi

# ---- final permission sweep ----
# Belt and suspenders on top of umask + rsync --chmod: whatever created a
# file under BACKUP_ROOT, nothing in it should be group/world readable when
# this script exits — app.db backups contain password hashes. Scoped to
# BACKUP_ROOT only; DATA_DIR (the source) is never touched.
chmod 700 "$BACKUP_ROOT_ABS"
chmod -R go-rwx "$BACKUP_ROOT_ABS"

log "done."
