# DentTime Monitoring Hotfix

This patch fixes the Docker backend image so that integration smoke testing and the metrics updater service can run inside Docker.

Changed files:
- `docker/Dockerfile.backend`
  - Copies `monitoring/`, `run_metrics_loop.py`, and `smoke_test_integration.py` into `/app`.
- `requirements-backend.txt`
  - Adds `httpx`, required by `fastapi.testclient.TestClient`.

After applying the patch:

```powershell
docker compose down --remove-orphans
docker compose build --no-cache api metrics_updater
docker compose up -d
docker compose ps
docker compose logs metrics_updater --tail=50
docker compose exec api python smoke_test_integration.py
docker compose exec api python monitoring/update_metrics.py
```
