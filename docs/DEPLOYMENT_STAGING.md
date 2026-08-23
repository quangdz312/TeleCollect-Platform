# CPU-only staging deployment

This covers building, starting, checking and safely stopping the CPU-only
staging stack (`docker-compose.prod.yml`, `Caddyfile`) on the CloudFly VPS.
For domain/VPS/DNS setup, see the deployment plan
(`TELECOLLECT_P111_CPU_IOVN_STAGING_DEPLOYMENT_MASTER_PLAN_V2_2.md`) —
this file only covers what runs from inside the repo.

Known limitations of this stack (see the plan, section 2.3):

- One backend instance only. `SessionManager` (teleop sessions) and
  `TrainingJobManager` (training jobs) keep state in RAM — a second replica
  would split that state across processes, and a restart drops any live
  teleop session / marks a running training job FAILED.
- SQLite + local files only. No PostgreSQL, no object storage in this stack.
- Training is disabled by default (`TRAINING_ENABLED=false`,
  `NEXT_PUBLIC_TRAINING_ENABLED=false`) because the CPU image only installs
  `requirements.txt`, not `requirements-train.txt`, and there is no GPU.

## Local HTTP smoke test (no DNS, no certificate)

Run this before touching the VPS at all — it only proves the images build
and Caddy routes requests correctly.

```bash
docker compose -f docker-compose.local.yml config
docker compose -f docker-compose.local.yml build
docker compose -f docker-compose.local.yml up -d
curl -f http://localhost:8080/
curl -f http://localhost:8080/health
docker compose -f docker-compose.local.yml ps
docker compose -f docker-compose.local.yml logs --tail=200
```

When done:

```bash
docker compose -f docker-compose.local.yml down
```

Never `down -v` — that deletes the named volume the backend used for its
SQLite/data during the test. It's fine to lose for a local smoke test, but
form the habit here since production never gets `-v`.

## Production build and start (on the VPS)

Prerequisites: `/srv/telecollect/secrets/.env.production` exists, filled in
from `.env.production.example`, `chmod 600`. `/srv/telecollect/data` exists
and is writable by the backend container's user.

```bash
cd /srv/telecollect/app

docker compose \
  --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml \
  config          # validate before doing anything real

docker compose \
  --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml \
  build

docker compose \
  --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml \
  up -d
```

Do not run `up` for the production stack against `localhost` with real
domain values — Caddy will try to obtain a Let's Encrypt certificate for a
domain that doesn't point at this machine yet and fail. Use
`docker-compose.local.yml` for anything before DNS is live.

## Checking status

```bash
docker compose \
  --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml \
  ps

docker compose \
  --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml \
  logs --tail=200
```

Or run the automated checks:

```bash
BASE_URL=https://<PROJECT_DOMAIN> ./scripts/verify_deployment.sh
```

## Restarting a single service

```bash
docker compose \
  --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml \
  restart backend
```

Restarting `backend` drops any live teleop session (RAM-only state) and
marks any running training job FAILED (training is disabled anyway in this
stack). It does not touch SQLite/files on the bind-mounted data directory.

## Stopping safely

```bash
docker compose \
  --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml \
  down
```

**Never `down -v`** on the production stack. `-v` removes the named volumes
(`caddy_data`, `caddy_config`, `caddy_logs`) — losing `caddy_data` throws
away the Let's Encrypt certificate and Caddy will need to re-request one
(rate-limited by Let's Encrypt). The bind-mounted `/srv/telecollect/data`
directory is not a Docker volume and survives `down` either way, but there
is no reason to ever pass `-v` here.

## Data directory and permissions

The backend container runs as `appuser` (see `Dockerfile`). The host path
bound to `/app/data` (`DATA_HOST_PATH` / `/srv/telecollect/data` by default)
must be writable by that container user. On the VPS:

```bash
sudo mkdir -p /srv/telecollect/data
sudo chown -R deploy:deploy /srv/telecollect/data
```

If the backend logs permission errors writing to `/app/data`, check the UID
the container's `appuser` actually has (`docker compose ... exec backend id`)
against the host directory's owner before reaching for `chmod 777`.
