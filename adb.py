import base64
import random
import re
import shlex
import struct
import subprocess
import threading
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


REFERENCE_SCREEN = (900, 1600)
_screen_sizes: dict[str, tuple[int, int]] = {}


def screen_size(serial: str) -> tuple[int, int] | None:
    """The instance's portrait resolution, from `wm size` (an override size is listed last, so it wins)."""
    if serial not in _screen_sizes:
        sizes = re.findall(r"(\d+)x(\d+)", _shell_output(serial, "wm", "size"))
        if not sizes:
            return None
        width, height = map(int, sizes[-1])
        _screen_sizes[serial] = (min(width, height), max(width, height))
    return _screen_sizes[serial]


def scaled(serial: str, x: int, y: int) -> tuple[int, int]:
    """Turns a point on a 900x1600 screen into the same point on the instance's screen."""
    size = screen_size(serial)
    if not size:
        return x, y
    return round(x * size[0] / REFERENCE_SCREEN[0]), round(y * size[1] / REFERENCE_SCREEN[1])


def tap(serial: str, x: int, y: int) -> None:
    """Taps through the touch screen when `prepare_touch` found it (a few milliseconds), else with `input tap`
    (a quarter of a second to a second: it starts a Java program on the device every time)."""
    jitter = config.TAP_JITTER_PIXELS
    if jitter:
        x += random.randint(-jitter, jitter)
        y += random.randint(-jitter, jitter)
    if _touch_tap(serial, x, y):
        return
    _device_call(serial, "shell", "input", "tap", str(x), str(y))
    time.sleep(TAP_SETTLE_SECONDS)


class _Touch:
    """The instance's touch screen and an adb shell kept open on it, so a tap is one write to the screen device."""

    def __init__(self, node: str, scale_x: float, scale_y: float, max_x: int, max_y: int, event_format: str,
                 button: bool, pressure: bool) -> None:
        self.node, self.scale_x, self.scale_y, self.max_x, self.max_y = node, scale_x, scale_y, max_x, max_y
        self.event_format, self.button, self.pressure = event_format, button, pressure
        self.shell: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def events(self, *items: tuple[int, int, int]) -> str:
        return base64.b64encode(b"".join(struct.pack(self.event_format, 0, 0, *item) for item in items)).decode()

    def command(self, x: int, y: int) -> str:
        raw_x = min(self.max_x, max(0, int(x * self.scale_x)))
        raw_y = min(self.max_y, max(0, int(y * self.scale_y)))
        down = [(3, 0x39, random.randint(1, 60000))] + ([(1, 0x14a, 1)] if self.button else [])
        down += [(3, 0x35, raw_x), (3, 0x36, raw_y)] + ([(3, 0x3a, 1)] if self.pressure else []) + [(0, 0, 0)]
        up = [(3, 0x39, -1)] + ([(1, 0x14a, 0)] if self.button else []) + [(0, 0, 0)]
        return (f"echo {self.events(*down)}|base64 -d >{self.node};"
                f"echo {self.events(*up)}|base64 -d >{self.node}" + chr(10))


_touch: dict[str, _Touch] = {}


def prepare_touch(serial: str) -> bool:
    """Finds the touch screen of `serial` and opens a shell on it, so `tap` skips `input tap`. False when the touch
    screen can't be used directly (rotated or scaled in a way this doesn't follow, or not writable): `tap` then
    keeps using `input tap`."""
    _touch.pop(serial, None)
    size = screen_size(serial)
    devices = _shell_output(serial, "getevent", "-pl").split("add device ")
    found = None
    for block in devices[1:]:
        max_x = re.search(r"ABS_MT_POSITION_X\s*:.*?max (\d+)", block)
        max_y = re.search(r"ABS_MT_POSITION_Y\s*:.*?max (\d+)", block)
        node = re.search(r"^\d+: (/dev/input/event\d+)", block)
        if max_x and max_y and node and "INPUT_PROP_DIRECT" in block:
            found = node.group(1), int(max_x.group(1)), int(max_y.group(1)), block
            break
    if not size or not found:
        return False
    node, max_x, max_y, block = found
    mapper = _shell_output(serial, "dumpsys", "input").split("DeviceType: touchScreen", 1)
    if len(mapper) < 2:
        return False
    xscale = re.search(r"XScale: ([\d.]+)", mapper[1])
    yscale = re.search(r"YScale: ([\d.]+)", mapper[1])
    turned = re.search(r"SurfaceOrientation: (\d)", mapper[1])
    if not xscale or not yscale or (turned and turned.group(1) != "0"):
        return False
    width, height = size
    if abs(float(xscale.group(1)) - width / (max_x + 1)) > 0.002:
        return False
    if abs(float(yscale.group(1)) - height / (max_y + 1)) > 0.002:
        return False
    check = _shell_output(serial, f": >{node} && echo AAAA | base64 -d | od -An -tx1 && "
                                  f"head -c 5 /system/bin/toybox | od -An -tx1")
    lines = check.splitlines()
    if len(lines) < 2 or lines[0].split() != ["00", "00", "00"] or len(lines[1].split()) != 5:
        return False
    event_format = "<qqHHi" if lines[1].split()[4] == "02" else "<iiHHi"
    _touch[serial] = _Touch(node, (max_x + 1) / width, (max_y + 1) / height, max_x, max_y, event_format,
                            "BTN_TOUCH" in block, "ABS_MT_PRESSURE" in block)
    return _touch_shell(_touch[serial], serial) is not None


def _touch_shell(touch: _Touch, serial: str) -> subprocess.Popen | None:
    if touch.shell is None or touch.shell.poll() is not None:
        try:
            touch.shell = subprocess.Popen([ADB_BIN, "-s", serial, "shell"], stdin=subprocess.PIPE,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=NO_WINDOW)
        except OSError:
            touch.shell = None
    return touch.shell


def _touch_tap(serial: str, x: int, y: int) -> bool:
    touch = _touch.get(serial)
    if touch is None:
        return False
    with touch.lock:
        for _ in range(2):
            shell = _touch_shell(touch, serial)
            if shell is None:
                return False
            try:
                shell.stdin.write(touch.command(x, y).encode())
                shell.stdin.flush()
                return True
            except OSError:
                shell.kill()
    return False


def close_touch() -> None:
    for touch in list(_touch.values()):
        if touch.shell is not None and touch.shell.poll() is None:
            touch.shell.kill()
    _touch.clear()


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


def unlink(serial: str, port: int) -> None:
    _run("-s", serial, "reverse", "--remove", f"tcp:{port}", check=False)


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
