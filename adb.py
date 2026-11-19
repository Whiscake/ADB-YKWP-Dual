import random
import shlex
import subprocess
import time

import config
import settings

ADB_BIN = settings.find_adb() or "adb"
TAP_SETTLE_SECONDS = 0.25
ADB_TIMEOUT_SECONDS = 10.0
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(*args: str, check: bool) -> None:
    subprocess.run([ADB_BIN, *args], capture_output=True, check=check, timeout=ADB_TIMEOUT_SECONDS,
                   creationflags=NO_WINDOW)


def restart_server() -> None:
    for args in (["start-server"], ["devices"]):
        _run(*args, check=False)


def connect(serial: str) -> None:
    _run("connect", serial, check=False)


def attached() -> set[str]:
    """Serials adb currently lists in the usable 'device' state (not offline or unauthorized)."""
    out = subprocess.run([ADB_BIN, "devices"], capture_output=True, text=True, check=False,
                         timeout=ADB_TIMEOUT_SECONDS, creationflags=NO_WINDOW).stdout
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


CLICKER_PACKAGES = ("com.puni.smartautoclicker.debug", "com.puni.smartautoclicker")
BRIDGE_RECEIVER = "com.buzbuz.smartautoclicker.BridgeSettingsReceiver"


def reverse(serial: str, port: int) -> None:
    """Makes 127.0.0.1:<port> on the device reach this computer through adb, on any emulator and past any firewall."""
    _device_call(serial, "reverse", f"tcp:{port}", f"tcp:{port}")


def is_reversed(serial: str, port: int) -> bool:
    out = subprocess.run([ADB_BIN, "-s", serial, "reverse", "--list"], capture_output=True, text=True, check=False,
                         timeout=ADB_TIMEOUT_SECONDS, creationflags=NO_WINDOW).stdout
    return f"tcp:{port} tcp:{port}" in out


def push_bridge(serial: str, port: int, token: str) -> None:
    """Gives the clicker the bridge address, port and token. Only adb may send this (the receiver requires DUMP)."""
    for package in CLICKER_PACKAGES:
        _run("-s", serial, "shell", "am", "broadcast", "-a", "com.buzbuz.smartautoclicker.SET_BRIDGE",
             "-n", f"{package}/{BRIDGE_RECEIVER}", "--es", "host", "127.0.0.1", "--ei", "port", str(port),
             "--es", "token", token, check=False)


def installed_clicker(serial: str) -> str | None:
    out = subprocess.run([ADB_BIN, "-s", serial, "shell", "pm", "list", "packages"], capture_output=True, text=True,
                         check=False, timeout=ADB_TIMEOUT_SECONDS, creationflags=NO_WINDOW).stdout.split()
    return next((package for package in CLICKER_PACKAGES if f"package:{package}" in out), None)


def _shell_output(serial: str, *args: str) -> str:
    try:
        return subprocess.run([ADB_BIN, "-s", serial, "shell", *args], capture_output=True, text=True, check=False,
                              timeout=ADB_TIMEOUT_SECONDS, creationflags=NO_WINDOW).stdout.strip()
    except subprocess.TimeoutExpired:
        return ""


def wait_until(check, timeout: float, interval: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(interval)
    return False


def uptime(serial: str) -> float | None:
    """Seconds since the emulator last started."""
    value = _shell_output(serial, "cat", "/proc/uptime").split()
    try:
        return float(value[0]) if value else None
    except ValueError:
        return None


def booted(serial: str) -> bool:
    return _shell_output(serial, "getprop", "sys.boot_completed") == "1"


def clicker_service_ready(serial: str, package: str) -> bool:
    return "SmartAutoClickerService" in _shell_output(serial, "dumpsys", "activity", "services", package)


def resume_clicker(serial: str, package: str, scenario: str | None = None) -> None:
    """Lets the clicker record the screen without the consent dialog, then has it play `scenario`, or its last one."""
    _device_call(serial, "shell", "appops", "set", package, "PROJECT_MEDIA", "allow")
    extra = ["--es", "scenario_name", shlex.quote(scenario)] if scenario else []
    _device_call(serial, "shell", "am", "start", "-n", f"{package}/com.buzbuz.smartautoclicker.activity.ResumeActivity",
                 *extra)


def clicker_playing(serial: str, package: str) -> bool:
    return package in _shell_output(serial, "dumpsys", "media_projection")


def game_running(serial: str, package: str) -> bool:
    return bool(_shell_output(serial, "pidof", package))


def start_game(serial: str, package: str) -> None:
    _device_call(serial, "shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")


def force_stop(serial: str, package: str) -> None:
    _device_call(serial, "shell", "am", "force-stop", package)
