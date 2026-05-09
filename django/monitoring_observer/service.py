from __future__ import annotations

import os
import signal
import time

from monitoring_observer.collector import collect_state, write_state


INTERVAL_SECONDS = float(os.environ.get("MONITORING_OBSERVER_INTERVAL_SECONDS", "1"))
running = True


def stop(signum, frame):
    global running
    running = False


def main() -> int:
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while running:
        write_state(collect_state())
        time.sleep(INTERVAL_SECONDS)
    write_state(collect_state())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
