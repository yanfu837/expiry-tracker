"""Keep signatures current and stop both children on shutdown or failure."""
import os
import signal
import subprocess
import sys
import time


def main():
    os.umask(0o077)
    if len(os.environ.get("DOCUMENT_PROCESSOR_TOKEN", "")) < 32:
        raise SystemExit("Set the document processor authentication token before starting.")
    config = "/app/document_service/freshclam.conf"
    result = subprocess.run(["freshclam", "--config-file="+config, "--stdout"], timeout=600, check=False)
    if result.returncode:
        raise SystemExit("Initial signature update failed; service not started.")
    children = []
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
        for child in children:
            if child.poll() is None:
                child.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        children.append(subprocess.Popen(["freshclam", "--config-file="+config, "--daemon", "--foreground"]))
        children.append(subprocess.Popen([
            "gunicorn", "document_service.application:application", "--bind", "0.0.0.0:"+os.environ.get("PORT", "10000"),
            "--workers", "1", "--timeout", "110", "--backlog", "4", "--error-logfile", "-",
        ]))
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(1)
    finally:
        requested_shutdown = stopping
        stop()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return 0 if requested_shutdown else 1


if __name__ == "__main__":
    sys.exit(main())
