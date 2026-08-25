# Backup and restore — CPU-only staging

Scripts: `scripts/backup_staging.sh`, `scripts/restore_staging.sh`. Both are
plain bash, no Python/Docker dependency of their own — they operate directly
on the host bind-mounted data directory (`/srv/telecollect/data` by default,
same path `docker-compose.prod.yml` mounts to `/app/data`). Both require
`sqlite3`, `sha256sum`, `tar` and `flock` (`rsync` additionally for backup);
all are either preinstalled on Debian/Ubuntu or a one-line `apt-get install`
— the scripts fail fast with that exact hint if one is missing.

## What gets backed up

| Source | Method | Destination |
|---|---|---|
| `app.db` (SQLite) | `sqlite3 .backup` — SQLite's own online backup API, safe against a concurrently-writing backend | `$BACKUP_ROOT/sqlite/app-<UTC timestamp>.db` + `.sha256` |
| `episodes/`, `datasets/`, `training/`, `review/` | `rsync -a`, incremental, never `--delete` | `$BACKUP_ROOT/artifacts/<subdir>/` + one `manifest.sha256` covering the whole mirror |
| (with `--full`) both of the above | `tar czf` | `$BACKUP_ROOT/archives/telecollect-full-<timestamp>.tar.gz` + `.sha256` |

Never backed up, on purpose:

- `tmp/` — upload-validation scratch space (`src/services/storage.py`), not
  meant to survive past the request that created it.
- Anything under `/srv/telecollect/secrets` (`.env.production`, `JWT_SECRET`)
  — the scripts only ever read the four artifact subdirectories and the
  SQLite file by explicit name, never a broad copy of `DATA_DIR`.

### Secrets must live outside `DATA_DIR` — this is a real, only partially
### closed gap, not a formality

**The actual safety boundary is operational, not something these scripts can
fully enforce: secrets belong under `/srv/telecollect/secrets`, never inside
`/srv/telecollect/data`.** The four backed-up subdirectories are an
allowlist *by directory name* (`episodes/`, `datasets/`, `training/`,
`review/`) — if a credential-shaped file ends up *inside* one of those
(e.g. someone drops `episodes/.env` there by mistake), it is inside the
allowlist and will be picked up.

As a second line of defense, `backup_staging.sh` scans each of the four
subdirectories before copying anything and **refuses to run** (does not
silently skip the file — aborts the whole backup) if it finds a file or
directory named `.env`, `.env.*`, `secrets`, `id_rsa`, `id_ed25519`,
`*.pem`, or `*.key`. This catches the common shapes of an accidentally
misplaced credential, but it is a pattern match, not a guarantee — it does
not catch a secret embedded in, say, a file with an unrelated name. Treat it
as a tripwire, not as license to store secrets under `DATA_DIR` on purpose.

## Permissions

`app.db` contains password hashes. Both scripts run under `umask 077` and
end with a recursive `chmod 700` / `chmod -R go-rwx` pass — scoped strictly
to `$BACKUP_ROOT` (backup) or `$TARGET` (restore), **never** to `DATA_DIR`,
which the scripts never `chmod`. This two-layer approach exists because
`rsync -a` and `cp -a` set permissions explicitly from the source file's
mode (via `chmod()`, not through umask-affected creation calls) — a
world-readable source would otherwise stay world-readable in the backup
regardless of umask. `rsync` is additionally called with
`--chmod=Du=rwx,Dgo=,Fu=rw,Fgo=` so the mirror is written owner-only from
the start, with the final sweep as a backstop covering everything else
(checksums, the manifest, tar/sqlite output).

Verify on the VPS after a run:

```bash
find /srv/telecollect/backups -perm -o+r    # should print nothing
stat -c '%a %n' /srv/telecollect/backups     # should be 700
```

## Concurrency

Both scripts use `flock` (non-blocking). There are two independent locks,
covering the read side and the write side separately:

- **Source lock — `backup_staging.sh` vs. everything else.**
  `backup_staging.sh` takes an **exclusive** lock on
  `$BACKUP_ROOT/.backup.lock` before touching anything (before the first
  copy, checksum, manifest write, or retention delete) and holds it for the
  entire run. A second `backup_staging.sh` invocation against the same
  `BACKUP_ROOT` fails immediately with a clear error instead of silently
  queuing or corrupting a file both processes touch at once.
  `restore_staging.sh` takes a **shared** lock on that same file before
  reading anything, if the file exists next to the resource being restored
  (colocated by convention — `restore_staging.sh` derives its location from
  `--sqlite-backup`/`--archive`/`--artifacts-source`). Multiple restores can
  hold this shared lock at once — reading the same backup root concurrently
  is fine, they never write there — while a running backup's exclusive lock
  blocks any of them from starting (it might delete or partially rewrite
  the very files being read). If the lock file isn't present (e.g.
  restoring from a backup copied to another machine without it), the check
  is skipped rather than failing a legitimate restore.

- **Target lock — restore vs. another restore into the same directory.**
  The source lock above says nothing about where a restore *writes*.
  `restore_staging.sh` also takes an **exclusive** lock on
  `<parent-of-target>/.<target-name>.restore.lock` — computed from
  `--target` itself, so it is scoped per destination directory, not shared
  globally. Two restores into two *different* targets never contend (each
  gets its own lock file) and both are allowed to succeed. Two restores
  aimed at the *same* target contend on the same lock file: the first to
  acquire it proceeds, the second fails immediately — before creating
  `app.db`, before copying a single artifact — rather than both passing the
  "is the target empty?" check and then racing to write it.

  The lock file lives in the target's *parent* directory, never inside the
  target itself, so it can never be mistaken for restored data or copied
  anywhere. It is intentionally left behind (empty) after a successful run:
  `flock`'s exclusivity comes from a process actively holding the lock, not
  from the file merely existing, so a leftover lock file from a finished
  (or crashed) run never blocks a later one. The kernel releases the lock
  automatically the moment the holding process exits — normally or via a
  crash — no manual cleanup required, and this script never unlinks a lock
  file while it might still be held (removing it out from under an active
  lock risks a second process locking a freshly-created file at the same
  path while the first still holds the original inode — silently defeating
  the lock).

## Directory layout

```
/srv/telecollect/backups/
├── .backup.lock                     (flock target, empty file)
├── sqlite/
│   ├── app-20260823-090000.db
│   ├── app-20260823-090000.db.sha256
│   └── ...                          (kept: RETENTION_COUNT, default 14)
├── artifacts/
│   ├── manifest.sha256              (sha256 of every file below, relative paths)
│   ├── episodes/...
│   ├── datasets/...
│   ├── training/...
│   └── review/...                   (one live mirror, not timestamped)
└── archives/                        (only written by --full)
    ├── telecollect-full-20260823-090000.tar.gz
    ├── telecollect-full-20260823-090000.tar.gz.sha256
    └── ...                          (kept: ARCHIVE_RETENTION_COUNT, default 3)
```

`BACKUP_ROOT` must live outside `DATA_DIR` — the script refuses to run
otherwise (protects against a backup destination that is itself inside the
directory being backed up).

## Running a backup

```bash
# Daily / pre-deploy: SQLite snapshot + incremental artifact mirror + manifest
./scripts/backup_staging.sh

# Before/at the start of/after the BTC grading window: also write a full
# tar.gz archive (see plan section 5.3 — not meant to run daily, retained
# separately and more sparsely than the sqlite snapshots)
./scripts/backup_staging.sh --full
```

Env overrides (all optional — defaults match the VPS layout in the
deployment plan):

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `/srv/telecollect/data` | Source — same path bind-mounted to `/app/data` |
| `BACKUP_ROOT` | `/srv/telecollect/backups` | Destination root |
| `RETENTION_COUNT` | `14` | SQLite snapshots to keep — must be an integer >= 1 |
| `ARCHIVE_RETENTION_COUNT` | `3` | Full archives to keep (`--full` only) — must be an integer >= 1 |

`RETENTION_COUNT`/`ARCHIVE_RETENTION_COUNT` are validated *before* any file
is created or deleted: `0`, a negative number, or a non-numeric value all
fail fast with a clear message and touch nothing. There is no "keep
everything forever" mode via `0` — pass a large number instead if that's
what you want.

The script fails fast (exit 1, clear message, nothing partially written that
looks like a successful backup) if: `sqlite3`/`rsync`/`sha256sum`/`tar`/`flock`
are missing, `DATA_DIR` or the database file don't exist, `BACKUP_ROOT` isn't
writable, `BACKUP_ROOT` resolves inside `DATA_DIR`, less than 512MB is free
at `BACKUP_ROOT`, a suspected secret file is found inside an artifact
subdirectory (see above), or another backup is already running.

### Scheduling

Not wired to cron by this change (out of scope for this checkpoint). To run
it daily on the VPS:

```
# /etc/cron.d/telecollect-backup
0 3 * * * deploy DATA_DIR=/srv/telecollect/data BACKUP_ROOT=/srv/telecollect/backups /srv/telecollect/app/scripts/backup_staging.sh >> /var/log/telecollect-backup.log 2>&1
```

## Restoring

**Always restores into a fresh directory you name explicitly. Never
overwrites `DATA_DIR`, never touches the running stack.**

```bash
# From a sqlite snapshot + the artifact mirror
./scripts/restore_staging.sh \
  --sqlite-backup /srv/telecollect/backups/sqlite/app-20260823-090000.db \
  --artifacts-source /srv/telecollect/backups/artifacts \
  --target /srv/telecollect/restore-test/2026-08-23

# From a --full archive
./scripts/restore_staging.sh \
  --archive /srv/telecollect/backups/archives/telecollect-full-20260823-090000.tar.gz \
  --target /srv/telecollect/restore-test/2026-08-23
```

Before touching anything, the script:

1. Takes a shared lock against a concurrently-running backup (see
   Concurrency above).
2. Refuses to run if `--target` already contains `app.db`, or isn't empty.
3. Verifies the SQLite backup/archive's `.sha256` checksum — refuses to
   proceed on a missing checksum file or a mismatch (corrupted or tampered
   backup).
4. If restoring artifacts (`--artifacts-source`, or an archive that
   contains a `manifest.sha256`), verifies **every file** in
   `manifest.sha256` against the source **before copying anything** —
   a modified or missing artifact stops the restore with a non-zero exit
   instead of being copied and discovered later. An artifact source with no
   manifest (a backup made before this existed) is refused outright for
   `--artifacts-source`; an archive with no manifest logs a warning and
   skips artifact verification (sqlite verification still applies).
5. After copying, opens the restored SQLite file read-only and runs
   `PRAGMA integrity_check` — a checksum match only proves the bytes are
   what was written at backup time, not that they form a valid database.

### Inspecting a restored copy safely

The script prints a ready-to-edit command at the end; the pattern is: point
a throwaway backend container at the restored directory instead of the live
one, on a different host port, and never touch the production compose stack:

```bash
docker run --rm -p 18000:8000 \
  -e DATABASE_URL="sqlite+aiosqlite:////app/data/app.db" \
  -e STORAGE_DIR=/app/data -e REVIEW_DIR=/app/data/review \
  -e JWT_SECRET=restore-inspection-only \
  -v /srv/telecollect/restore-test/2026-08-23:/app/data \
  <backend image>
```

Then browse `http://localhost:18000/health`, log in with a known test
account from that snapshot, confirm the data you expect is there.

### Promoting a restored copy to live data (manual, deliberate — never automated)

Only do this after you have inspected the restored copy and are certain.

```bash
cd /srv/telecollect/app

# 1. Stop the stack so nothing writes to /srv/telecollect/data mid-swap.
docker compose --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml stop backend

# 2. Move the current (possibly broken) data dir aside — do not delete it yet.
sudo mv /srv/telecollect/data /srv/telecollect/data.before-restore-$(date -u +%Y%m%d-%H%M%S)

# 3. Move the restored, already-inspected copy into place.
sudo mv /srv/telecollect/restore-test/2026-08-23 /srv/telecollect/data
sudo chown -R deploy:deploy /srv/telecollect/data

# 4. Restart and smoke test.
docker compose --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml start backend
curl -fsS https://<PROJECT_DOMAIN>/health
```

Keep the `data.before-restore-*` directory until you're confident the
promoted restore is good — delete it manually once satisfied, this script
set does not do that for you either.

## R2 (optional, not implemented in this change)

The plan allows optionally uploading backups to a private Cloudflare R2
bucket via `rclone copy` (never `sync --delete`) once R2 credentials exist.
**Not built or tested here** — no R2 credentials were available to test
against, and none of the following is implemented. `scripts/backup_staging.sh`
only writes to a local `BACKUP_ROOT`; adding an R2 upload step is a
follow-up, not a change to how backups are produced.

If/when this is built, it must not be a plain `rclone copy $BACKUP_ROOT
r2:bucket/` of the raw backup:

- **`app.db` contains account data and password hashes.** Uploading the raw
  SQLite backup (or a raw `--full` archive, which contains it) to any
  third-party object storage — even a private bucket — means that
  provider's access controls become part of your security boundary for
  every user's credential. Encrypt client-side *before* upload, e.g. an
  `rclone crypt` remote layered on top of the R2 remote, or `age`/`gpg`
  encrypting the archive first and uploading only the encrypted blob.
  Losing the encryption key/password means losing the backup — store it
  somewhere other than next to the backup itself.
- The R2 bucket must be **private** (no public access), and the R2 API
  token used must be **least-privilege** — scoped to only that bucket, not
  an account-wide token, so a leaked credential doesn't expose unrelated
  data.
- `rclone copy`, never `rclone sync --delete` (same reasoning as the local
  `rsync` mirror above — see the deployment plan section 4.5).

Until this exists, copy `$BACKUP_ROOT` off the VPS by hand (e.g.
`rsync`/`scp` to your machine, over SSH — already encrypted in transit) —
see the deployment plan section 4.5 for the interim options.

## Tested (temporary/fake data only, see verification below)

- `sqlite3 .backup` snapshot + checksum, verified byte-identical to the
  live database via `sqlite3 ... SELECT` comparison.
- Incremental artifact mirror across three runs: file A alone, then A+B,
  then A modified in place — the mirror correctly picked up the modified
  content of A (rsync compares mtime+size, and the modification changed
  both), not just the new file B.
- A file removed from the source afterwards remained in the backup mirror
  (no `--delete`).
- Retention: `RETENTION_COUNT=2` / `ARCHIVE_RETENTION_COUNT=2` both correctly
  pruned down to exactly the configured count across repeated runs, without
  touching files outside the `app-*.db` / `telecollect-full-*.tar.gz`
  pattern. Invalid values (`0`, `-1`, `abc`) are rejected before any file is
  touched.
- Restore from both `--sqlite-backup` and `--archive`, into a fresh target;
  restored SQLite rows and restored artifact files matched the originals
  byte-for-byte; `PRAGMA integrity_check` passed.
- Restore refuses a non-empty `--target` even when it doesn't contain
  `app.db` (e.g. an unrelated leftover file).
- Restore refuses a backup with no `.sha256` file, and refuses a tampered
  backup file whose bytes no longer match its `.sha256` record — verified
  against the actual file being restored, not a stale reference to the
  untouched original elsewhere (see the fix history: an earlier version
  recorded the checksum's filename as an absolute path, which made
  verification silently check the original file instead of the copy being
  restored — checksums now record a relative basename and are verified
  after `cd`-ing into the same directory as the file, so this class of bug
  can't recur).
- Restore refuses `--artifacts-source` with no `manifest.sha256`, and
  refuses when a file in the manifest has been modified or deleted —
  verified by corrupting one artifact file and deleting another, both
  independently caught before any file was copied.
- A fake secret placed outside `DATA_DIR`, and a fake file under
  `DATA_DIR/tmp/`, were both confirmed absent from every backup output
  (sqlite dir, artifacts mirror, manifest, and the full archive). A fake
  secret placed *inside* `DATA_DIR/episodes/` was correctly caught by the
  suspicious-file scan, which aborted the backup before copying anything.
- Two `backup_staging.sh` runs started concurrently against the same
  `BACKUP_ROOT`: the second failed immediately with a clear lock error
  before writing anything; the first completed normally.
- Two `restore_staging.sh` runs started concurrently at the *same*
  `--target`: one succeeded, the other failed immediately with the target
  lock error, before creating `app.db` or copying any artifact — the final
  `app.db` passed `PRAGMA integrity_check`. Two runs at *different* targets
  both succeeded. A run started while a backup was mid-flight against the
  same source correctly interacted with the (unchanged) source-side shared
  lock. After a run finished, its target lock file (left behind, empty) did
  not block a later legitimate restore — confirmed by immediately restoring
  into a fresh target using the same locking code path. No `.restore.lock`
  file ever appeared inside a restored target directory (verified with
  `find -mindepth 2 -name '*.restore.lock'`), and every lock file's
  permissions were `600`.
- Backup and restore output permissions verified with `stat`/`find -perm`:
  nothing under `$BACKUP_ROOT` or a restore `$TARGET` is group- or
  world-readable.

Not tested (no VPS / real data yet): a real multi-GB artifact set, R2
upload (no credentials available), and running this under actual cron on
the VPS.
