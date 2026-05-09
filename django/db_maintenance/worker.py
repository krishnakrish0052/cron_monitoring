from __future__ import annotations

import logging
import time

from db_maintenance.core import execute_job
from db_maintenance.store import claim_next_job, finish_job


logging.basicConfig(level=logging.INFO, format="[db-maintenance] %(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger(__name__)


def run_forever(poll_seconds: float = 3.0) -> None:
    LOG.info("worker started")
    while True:
        job = claim_next_job()
        if not job:
            time.sleep(poll_seconds)
            continue
        started = time.monotonic()
        LOG.info("running job id=%s project=%s action=%s table=%s.%s", job["id"], job["project"], job["action"], job["schema_name"], job["table_name"])
        try:
            output = execute_job(job)
            finish_job(job["id"], "success", output=output, duration_seconds=time.monotonic() - started)
            LOG.info("job id=%s success", job["id"])
        except Exception as exc:
            finish_job(job["id"], "failure", error=str(exc), duration_seconds=time.monotonic() - started)
            LOG.exception("job id=%s failure", job["id"])


if __name__ == "__main__":
    run_forever()

