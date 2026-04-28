# Unified Docker Compose Design

**Date:** 2026-04-28  
**Status:** Approved

## Goal

Consolidate the two existing Docker Compose files into a single `docker-compose.yml` at the project root, using Docker Compose profiles to allow selective stack startup. Motivations: demo convenience (one command starts everything) and developer ergonomics (no need to remember which file controls which stack).

## Current State

Two compose files exist:

| File | Services |
|---|---|
| `docker/docker-compose.yml` | postgres, mlflow, airflow-init, airflow-webserver, airflow-scheduler |
| `docker/compose/frontend-backend.yml` | api, frontend, prometheus, grafana, metrics_updater |

`Monitoring-Alerting/docker-compose.yml` referenced in CLAUDE.md does not exist — monitoring services are already inside `frontend-backend.yml`.

## Target State

### File Structure

- **New:** `docker-compose.yml` at project root (unified file with profiles)
- **New:** `Makefile` at project root (convenience aliases)
- **Moved:** `docker/.env` → `.env` at project root
- **Deleted:** `docker/docker-compose.yml`
- **Deleted:** `docker/compose/frontend-backend.yml`
- **Unchanged:** all Dockerfiles in `docker/`

### Profiles

| Profile | Services | Purpose |
|---|---|---|
| `training` | postgres, mlflow, airflow-init, airflow-webserver, airflow-scheduler | Feature engineering + model retraining pipeline |
| `serving` | api, frontend, prometheus, grafana, metrics_updater | Web app + monitoring |

### Commands

```bash
# Full demo mode — start everything
docker compose --profile training --profile serving up -d

# Feature engineering stack only
docker compose --profile training up -d

# Web app + monitoring only
docker compose --profile serving up -d

# Stop everything
docker compose down
```

### Makefile Targets

```makefile
up        ## Start everything (demo mode)
up-train  ## Start feature engineering stack only
up-serve  ## Start web app + monitoring only
down      ## Stop all containers
validate  ## Check compose file syntax
```

## Key Technical Changes

### Build Contexts

All build contexts change from relative paths (`../`, `../../`) to `.` (project root), since the compose file now lives at root. Dockerfile paths stay the same (`docker/Dockerfile.airflow`, etc.).

```yaml
# Before
build:
  context: ..
  dockerfile: docker/Dockerfile.airflow

# After
build:
  context: .
  dockerfile: docker/Dockerfile.airflow
```

### Volume Mounts

All volume mount host paths update from `../` or `../../` prefixes to `./`:

```yaml
# Before (from docker/compose/frontend-backend.yml)
- ../../data:/app/data

# After
- ./data:/app/data
```

### Environment File

`docker/.env` moves to `.env` at project root. The `env_file` reference in airflow services updates from `- .env` (relative to `docker/`) to `- .env` (relative to root — no change in syntax, just file location changes).

### Shared Volume

Both stacks already reference `denttime-dvc-store` by the same name. In the unified file this volume is declared once and shared naturally between the `training` and `serving` services that mount it.

### Port Mapping (no conflicts)

| Service | Port | Profile |
|---|---|---|
| airflow-webserver | 8080 | training |
| mlflow | 5000 | training |
| api | 8000 | serving |
| frontend | 5173 | serving |
| prometheus | 9090 | serving |
| grafana | 3000 | serving |

## Verification Plan

```bash
# 1. Syntax check
docker compose config --quiet

# 2. Training stack
docker compose --profile training up -d
# Verify: :8080 (Airflow), :5000 (MLflow)

# 3. Serving stack
docker compose --profile serving up -d
# Verify: :5173 (Frontend), :8000/docs (API), :3000 (Grafana)

# 4. Full demo
docker compose --profile training --profile serving up -d
# Verify: all ports above respond simultaneously

# 5. Teardown
make down
```

## What Does Not Change

- All Dockerfile contents
- All service configurations (environment variables, healthchecks, dependencies)
- Volumes and their data (named volumes persist across compose file changes)
- CLAUDE.md commands section (will be updated to reflect new single-file workflow)
