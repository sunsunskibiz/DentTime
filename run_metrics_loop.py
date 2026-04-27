from __future__ import annotations

import logging
import time

from monitoring.update_metrics import main

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


while True:
    try:
        main()
        logging.info("DentTime monitoring state refreshed")
    except Exception:
        logging.exception("DentTime monitoring state refresh failed")
    time.sleep(15)
