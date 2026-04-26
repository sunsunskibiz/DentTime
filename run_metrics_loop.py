from __future__ import annotations

import time

from monitoring.update_metrics import main as update_metrics


while True:
    try:
        update_metrics()
    except Exception as exc:
        print(f"metrics updater failed: {exc}", flush=True)
    time.sleep(15)
