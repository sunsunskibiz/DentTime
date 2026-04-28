# Unified Docker Compose Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace two separate Docker Compose files with a single root-level `docker-compose.yml` using profiles (`training`, `serving`) so one command starts the full stack.

**Architecture:** One `docker-compose.yml` at the project root declares all services; each service is tagged with either the `training` profile (Airflow + MLflow) or `serving` profile (web app + monitoring). Running with both profiles active starts everything for demos. A Makefile provides short aliases.

**Tech Stack:** Docker Compose v2 (profiles feature), GNU Make

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `docker-compose.yml` | Unified compose with `training` and `serving` profiles |
| Modify | `Makefile` | Add `up`, `up-train`, `up-serve`, `down`, `validate` targets |
| Move | `docker/.env` → `.env` | Environment variables for Airflow services |
| Delete | `docker/docker-compose.yml` | Replaced by root-level file |
| Delete | `docker/compose/frontend-backend.yml` | Replaced by root-level file |
| Modify | `CLAUDE.md` | Update Commands section to use new compose commands |

---

## Task 1: Create root-level `docker-compose.yml`

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create the file**

Create `docker-compose.yml` at the project root with this exact content:

```yaml
# ============================================================
# DentTime — Unified Compose
# Usage:
#   make up          → start everything (demo)
#   make up-train    → start Airflow + MLflow only
#   make up-serve    → start web app + monitoring only
#   make down        → stop everything
# ============================================================

x-airflow-common: &airflow-common
  build:
    context: .
    dockerfile: docker/Dockerfile.airflow
  env_file:
    - .env
  environment:
    AIRFLOW__CORE__EXECUTOR: LocalExecutor
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://airflow:airflow@postgres/airflow
    AIRFLOW__CORE__FERNET_KEY: ''
    AIRFLOW__WEBSERVER__SECRET_KEY: 'denttime-local-dev-secret'
    AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION: 'true'
    AIRFLOW__CORE__LOAD_EXAMPLES: 'false'
    MLFLOW_TRACKING_URI: http://mlflow:5000
  volumes:
    - ./:/opt/airflow/project
    - ./airflow/dags:/opt/airflow/dags
    - ./data/raw:/opt/airflow/data/raw:ro
    - denttime-interim:/opt/airflow/data/interim
    - denttime-dvc-store:/opt/airflow/dvc-store
    - mlflow-artifacts:/mlflow/artifacts
  depends_on:
    postgres:
      condition: service_healthy
    mlflow:
      condition: service_healthy
  profiles: [training]

services:
  # ── Training profile ───────────────────────────────────────
  postgres:
    image: postgres:15
    profiles: [training]
    environment:
      POSTGRES_USER: airflow
      POSTGRES_PASSWORD: airflow
      POSTGRES_DB: airflow
    volumes:
      - postgres-db:/var/lib/postgresql/data
      - ./docker/init-db.sql:/docker-entrypoint-initdb.d/init-db.sql:ro
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "airflow"]
      interval: 5s
      retries: 10
      start_period: 30s

  mlflow:
    build:
      context: .
      dockerfile: docker/Dockerfile.mlflow
    profiles: [training]
    command: >
      mlflow server
      --host 0.0.0.0
      --port 5000
      --backend-store-uri postgresql+psycopg2://airflow:airflow@postgres/mlflow
      --default-artifact-root /mlflow/artifacts
      --serve-artifacts
      --allowed-hosts '*'
    ports:
      - "5000:5000"
    volumes:
      - mlflow-artifacts:/mlflow/artifacts
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')"]
      interval: 20s
      retries: 10
      start_period: 120s
    restart: unless-stopped

  airflow-init:
    <<: *airflow-common
    user: root
    command:
      - bash
      - -c
      - |
          set -e
          chown -R 50000:0 /opt/airflow/data/interim /opt/airflow/dvc-store
          chown -R 50000:0 /opt/airflow/data/interim /opt/airflow/dvc-store /mlflow/artifacts
          su -s /bin/bash airflow -c '
            set -e
            airflow db migrate
            airflow users list | grep -q admin || \
              airflow users create \
                --username admin --password admin \
                --role Admin \
                --email admin@denttime.local \
                --firstname Admin --lastname Admin
            cd /opt/airflow/project && \
              (dvc remote add -d localremote /opt/airflow/dvc-store --local --force \
               || echo "DVC remote setup skipped")
          '
    restart: "no"

  airflow-webserver:
    <<: *airflow-common
    command: airflow webserver
    ports:
      - "8080:8080"
    healthcheck:
      test: ["CMD", "curl", "--fail", "http://localhost:8080/health"]
      interval: 30s
      retries: 3
    depends_on:
      airflow-init:
        condition: service_completed_successfully

  airflow-scheduler:
    <<: *airflow-common
    command: airflow scheduler
    depends_on:
      airflow-init:
        condition: service_completed_successfully

  # ── Serving profile ────────────────────────────────────────
  api:
    build:
      context: .
      dockerfile: docker/Dockerfile.backend
    profiles: [serving]
    container_name: denttime_api
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./artifacts:/app/artifacts
      - ./src/features/artifacts:/app/src/features/artifacts
      - ./monitoring:/app/monitoring
      - denttime-dvc-store:/opt/airflow/dvc-store

  prometheus:
    image: prom/prometheus:v2.54.1
    profiles: [serving]
    container_name: denttime_prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro
    depends_on:
      - api

  grafana:
    image: grafana/grafana:11.2.0
    profiles: [serving]
    container_name: denttime_grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - ./grafana/provisioning/datasources:/etc/grafana/provisioning/datasources:ro
      - ./grafana/provisioning/dashboards:/etc/grafana/provisioning/dashboards:ro
      - ./grafana/dashboards:/etc/grafana/dashboards:ro
    depends_on:
      - prometheus

  metrics_updater:
    build:
      context: .
      dockerfile: docker/Dockerfile.backend
    profiles: [serving]
    container_name: denttime_metrics_updater
    command: python run_metrics_loop.py
    volumes:
      - ./data:/app/data
      - ./artifacts:/app/artifacts
      - ./src/features/artifacts:/app/src/features/artifacts
      - ./monitoring:/app/monitoring
    depends_on:
      - api

  frontend:
    build:
      context: .
      dockerfile: docker/Dockerfile.frontend
    profiles: [serving]
    container_name: denttime_frontend
    ports:
      - "5173:5173"
    depends_on:
      - api
    environment:
      - VITE_API_URL=http://localhost:8000

volumes:
  postgres-db:
  denttime-interim:
  denttime-dvc-store:
  mlflow-artifacts:
```

- [ ] **Step 2: Validate syntax**

```bash
docker compose config --quiet
```

Expected: no output, exit code 0. If you see an error, check indentation around the `<<: *airflow-common` blocks — YAML anchors are sensitive to whitespace.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add unified root-level docker-compose with profiles"
```

---

## Task 2: Add Docker targets to `Makefile`

**Files:**
- Modify: `Makefile`

The existing `Makefile` has a `dvc-commit` target. Append the Docker targets below it — do not remove or modify the existing content.

- [ ] **Step 1: Add targets to Makefile**

Append to the bottom of `Makefile`:

```makefile

.PHONY: up up-train up-serve down validate

up: ## Start all stacks (demo mode)
	docker compose --profile training --profile serving up -d

up-train: ## Start feature engineering stack (Airflow + MLflow)
	docker compose --profile training up -d

up-serve: ## Start web app + monitoring stack
	docker compose --profile serving up -d

down: ## Stop all containers
	docker compose down

validate: ## Check compose file syntax
	docker compose config --quiet && echo "Compose config OK"
```

- [ ] **Step 2: Verify targets are callable**

```bash
make validate
```

Expected output:
```
Compose config OK
```

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "feat: add docker make targets (up, up-train, up-serve, down, validate)"
```

---

## Task 3: Move `.env` to project root

**Files:**
- Move: `docker/.env` → `.env`

The `.env` file contains `DAGSHUB_USER` and `DAGSHUB_TOKEN` used by Airflow services. The new root-level compose file expects `.env` at the project root.

- [ ] **Step 1: Copy the file**

```bash
cp docker/.env .env
```

- [ ] **Step 2: Verify the root `.env` has the same content**

```bash
cat .env
```

Expected output:
```
DAGSHUB_USER=sunsunskibiz
DAGSHUB_TOKEN=<your-dagshub-token>
```

- [ ] **Step 3: Re-validate compose with the env file in place**

```bash
docker compose config --quiet
```

Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add .env
git commit -m "feat: move .env to project root for unified compose"
```

---

## Task 4: Delete old compose files

**Files:**
- Delete: `docker/docker-compose.yml`
- Delete: `docker/compose/frontend-backend.yml`

- [ ] **Step 1: Remove the old files**

```bash
git rm docker/docker-compose.yml docker/compose/frontend-backend.yml
```

- [ ] **Step 2: Confirm the compose directory is now empty (or gone)**

```bash
ls docker/compose/
```

Expected: `ls: cannot access 'docker/compose/': No such file or directory` (git rm removes the file; if the directory is empty git will leave it, which is fine).

- [ ] **Step 3: Validate the root compose still works**

```bash
docker compose config --quiet
```

Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: remove old per-stack compose files superseded by root docker-compose.yml"
```

---

## Task 5: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

Update the **Commands** section to reflect the new single-file workflow.

- [ ] **Step 1: Replace the Feature Engineering Pipeline block**

Find this block in `CLAUDE.md`:

```markdown
### Feature Engineering Pipeline (Airflow + MLflow)
```bash
# Start stack (Airflow :8080, MLflow :5000, Postgres)
cd docker/ && docker compose up --build -d
```

Replace with:

```markdown
### Feature Engineering Pipeline (Airflow + MLflow)
```bash
# Start stack (Airflow :8080, MLflow :5000, Postgres)
make up-train
# or: docker compose --profile training up --build -d
```

- [ ] **Step 2: Replace the Frontend + Backend block**

Find this block:

```markdown
### Frontend + Backend (Inference)
```bash
docker compose -f docker/compose/frontend-backend.yml up --build
# Backend: http://localhost:8000/docs  Frontend: http://localhost:5173
```

Replace with:

```markdown
### Frontend + Backend + Monitoring
```bash
make up-serve
# or: docker compose --profile serving up --build -d
# Backend: http://localhost:8000/docs  Frontend: http://localhost:5173
# Prometheus: http://localhost:9090    Grafana: http://localhost:3000
```

- [ ] **Step 3: Add a demo/full-stack block after the above**

After the Frontend + Backend block, insert:

```markdown
### Full Stack (Demo Mode)
```bash
make up          # start all stacks
make down        # stop all stacks
make validate    # check compose syntax
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md commands for unified docker-compose"
```

---

## Smoke Test (after all tasks)

Run these in order to verify the full setup end-to-end:

```bash
# 1. Syntax
make validate

# 2. Training stack
make up-train
# Wait ~2 minutes for airflow-init to complete, then:
curl -s http://localhost:8080/health   # expected: {"metadatabase":{"status":"healthy"},...}
curl -s http://localhost:5000/health   # expected: {"status":"OK"}
make down

# 3. Serving stack
make up-serve
# Wait ~30 seconds, then:
curl -s http://localhost:8000/docs     # expected: FastAPI Swagger HTML
curl -s http://localhost:9090/-/ready  # expected: Prometheus is Ready.
make down

# 4. Full demo
make up
# Verify all ports above respond simultaneously
make down
```
