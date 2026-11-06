"""Private exec guardian: caller pipe EOF cancels and reaps its worker group."""

import json
import os
import selectors
import sys
import threading
import time
from pathlib import Path


def main() -> None:
    request = json.loads(Path(sys.argv[1]).read_text())
    sys.path[:] = request["path"]
    for name, value in request["prefixes"].items():
        setattr(sys, name, value)
    from pytest_leela._timeout_process import DirectTestProcess, ParentExited

    worker = DirectTestProcess()

    stop_read, stop_write = os.pipe()

    def watch_parent() -> None:
        with selectors.DefaultSelector() as selector:
            selector.register(sys.stdin.fileno(), selectors.EVENT_READ)
            selector.register(stop_read, selectors.EVENT_READ)
            ready = selector.select()
            if any(key.fd == stop_read for key, _ in ready):
                return
            worker.reply.put(ParentExited("leela: caller exited"))

    watcher = threading.Thread(target=watch_parent, daemon=False)
    watcher.start()
    try:
        try:
            response = worker.run(
                json.dumps(request).encode(), max(0, request["deadline"] - time.monotonic()),
            )
            envelope = {"result": json.loads(response)}
        except Exception as exc:
            envelope = {"failure": type(exc).__name__, "message": str(exc)}
    finally:
        os.write(stop_write, b"x")
        watcher.join(timeout=2)
        os.close(stop_write)
        os.close(stop_read)
        if watcher.is_alive():
            raise RuntimeError("leela: parent lifetime watcher did not stop")
    sys.stdout.write(json.dumps(envelope) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
