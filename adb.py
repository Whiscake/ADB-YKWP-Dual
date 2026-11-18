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


def attached() -> set[str]:
    """Serials adb currently lists in the usable 'device' state (not offline or unauthorized)."""
    out = subprocess.run([ADB_BIN, "devices"], capture_output=True, text=True, check=False,
                         timeout=ADB_TIMEOUT_SECONDS).stdout
    rows = (line.split() for line in out.splitlines()[1:])
    return {row[0] for row in rows if len(row) == 2 and row[1] == "device"}


def ensure_connected(serial: str) -> bool:
    """Connects `serial` unless adb already lists it. True when it is attached afterwards."""
    if serial in attached():
        return True
    connect(serial)
    return serial in attached()


def reconnect_missing(serials) -> dict[str, bool]:
    """Reconnects every serial adb doesn't list. Returns {serial: attached afterwards} for those that were missing."""
    have = attached()
    result = {}
    for serial in serials:
        if serial not in have:
            connect(serial)
            result[serial] = serial in attached()
    return result


def _device_call(serial: str, *args: str) -> None:
    """Runs an adb command on one device; if it fails, reconnects the device and tries once more."""
    try:
        _run("-s", serial, *args, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        if not ensure_connected(serial):
            raise
        _run("-s", serial, *args, check=True)


def tap(serial: str, x: int, y: int) -> None:
    jitter = config.TAP_JITTER_PIXELS
    if jitter:
        x += random.randint(-jitter, jitter)
        y += random.randint(-jitter, jitter)
    _device_call(serial, "shell", "input", "tap", str(x), str(y))
    time.sleep(TAP_SETTLE_SECONDS)


def force_stop(serial: str, package: str) -> None:
    _device_call(serial, "shell", "am", "force-stop", package)
