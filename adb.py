import random
import subprocess
import time

import config
import settings

ADB_BIN = settings.find_adb() or "adb"
TAP_SETTLE_SECONDS = 0.25
ADB_TIMEOUT_SECONDS = 10.0


def _run(*args: str, check: bool) -> None:
    subprocess.run([ADB_BIN, *args], capture_output=True, check=check, timeout=ADB_TIMEOUT_SECONDS)


def restart_server() -> None:
    for args in (["start-server"], ["devices"]):
        _run(*args, check=False)


def connect(serial: str) -> None:
    _run("connect", serial, check=False)


def tap(serial: str, x: int, y: int) -> None:
    jitter = config.TAP_JITTER_PIXELS
    if jitter:
        x += random.randint(-jitter, jitter)
        y += random.randint(-jitter, jitter)
    _run("-s", serial, "shell", "input", "tap", str(x), str(y), check=True)
    time.sleep(TAP_SETTLE_SECONDS)


def force_stop(serial: str, package: str) -> None:
    _run("-s", serial, "shell", "am", "force-stop", package, check=True)
