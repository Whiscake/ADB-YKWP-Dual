"""Finds the emulator instances on this computer (BlueStacks and LDPlayer) and their adb state."""
import platform
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

CLICKER_PACKAGES = ("com.puni.smartautoclicker.debug", "com.puni.smartautoclicker")
CLICKER_APK_URL = "https://github.com/Whiscake/ADB-YKWP-Dual/releases/latest/download/smartautoclicker-debug.apk"
DOWNLOAD_DIR = "/sdcard/Download"
BACKUP_RECEIVER = "com.buzbuz.smartautoclicker.BackupReceiver"
EXPORT_FILE = "dual_export.zip"
IMPORT_FILE = "dual_import.zip"
WINDOWS = platform.system() == "Windows"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if WINDOWS else 0


@dataclass
class Emulator:
    name: str
    serial: str
    running: bool
    adb_enabled: bool
    attached: bool = False
    clicker: bool | None = None

    @property
    def state(self) -> str:
        if not self.running:
            return "off"
        if not self.adb_enabled:
            return "ADB disabled"
        return "ready" if self.attached else "not attached"


def _run(args: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)


def attached(adb: str) -> set[str]:
    out = _run([adb, "devices"]).stdout
    rows = (line.split() for line in out.splitlines()[1:])
    return {row[0] for row in rows if len(row) == 2 and row[1] == "device"}


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _bluestacks_conf() -> Path | None:
    candidates = [Path("C:/ProgramData/BlueStacks_nxt/bluestacks.conf"),
                  Path("/Users/Shared/Library/Application Support/BlueStacks/bluestacks.conf")]
    return next((path for path in candidates if path.exists()), None)


def _bluestacks() -> list[Emulator]:
    conf = _bluestacks_conf()
    if not conf:
        return []
    values = dict(re.findall(r'^(bst\.instance\.[^=]+)="(.*)"$', conf.read_text(errors="ignore"), re.M))
    found = []
    for key, port in values.items():
        match = re.fullmatch(r"bst\.instance\.([^.]+)\.status\.adb_port", key)
        if not match or not port.isdigit():
            continue
        instance = match[1]
        name = values.get(f"bst.instance.{instance}.display_name") or instance
        enabled = values.get(f"bst.instance.{instance}.enable_adb_access", "1") == "1"
        serial = f"emulator-{int(port) - 1}"
        found.append(Emulator(name, serial, _port_open(int(port)), enabled))
    return found


def _ldplayer_console() -> Path | None:
    if not WINDOWS:
        return None
    import winreg
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key in (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\LDPlayer9",
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\LDPlayer9"):
            try:
                with winreg.OpenKey(root, key) as handle:
                    icon = winreg.QueryValueEx(handle, "DisplayIcon")[0]
            except OSError:
                continue
            console = Path(icon.strip('"').split(",")[0]).with_name("ldconsole.exe")
            if console.exists():
                return console
    fallback = Path("C:/LDPlayer/LDPlayer9/ldconsole.exe")
    return fallback if fallback.exists() else None


def _ldplayer() -> list[Emulator]:
    console = _ldplayer_console()
    if not console:
        return []
    found = []
    for line in _run([str(console), "list2"]).stdout.splitlines():
        parts = line.split(",")
        if len(parts) < 5 or not parts[0].isdigit():
            continue
        found.append(Emulator(parts[1], f"emulator-{5554 + 2 * int(parts[0])}", parts[4] == "1", True))
    return found


def reboot(serial: str) -> bool:
    """Restarts an LDPlayer instance through ldconsole. Other emulators are not supported."""
    console = _ldplayer_console()
    port = int(serial.rsplit("-", 1)[-1].rsplit(":", 1)[-1])
    index = (port - 5554) // 2 if serial.startswith("emulator-") else (port - 5555) // 2
    if not console or index < 0:
        return False
    return _run([str(console), "reboot", "--index", str(index)]).returncode == 0


def discover(adb: str) -> list[Emulator]:
    have = attached(adb)
    found = _bluestacks() + _ldplayer()
    known = set()
    for emulator in found:
        port = int(emulator.serial.rsplit("-", 1)[1])
        for serial in (emulator.serial, f"127.0.0.1:{port + 1}"):
            if serial in have:
                emulator.serial, emulator.attached, emulator.running = serial, True, True
        known.update({emulator.serial, f"emulator-{port}", f"127.0.0.1:{port + 1}"})
    found += [Emulator(serial, serial, True, True, True) for serial in sorted(have - known)]
    return sorted(found, key=lambda e: (not e.attached, not e.running, e.name.lower()))


def connect(adb: str, emulator: Emulator) -> bool:
    if emulator.attached or not emulator.running or not emulator.adb_enabled:
        return emulator.attached
    port = int(emulator.serial.rsplit("-", 1)[-1].rsplit(":", 1)[-1])
    _run([adb, "connect", f"127.0.0.1:{port + 1}"])
    have = attached(adb)
    for serial in (emulator.serial, f"127.0.0.1:{port + 1}"):
        if serial in have:
            emulator.serial, emulator.attached = serial, True
    return emulator.attached


def has_clicker(adb: str, serial: str) -> bool:
    out = _run([adb, "-s", serial, "shell", "pm", "list", "packages"]).stdout
    return any(f"package:{package}" in out.split() for package in CLICKER_PACKAGES)


def install_apk(adb: str, serial: str, apk: Path) -> str:
    result = _run([adb, "-s", serial, "install", "-r", str(apk)], timeout=180)
    return (result.stdout + result.stderr).strip().splitlines()[-1] if (result.stdout + result.stderr).strip() else ""


def list_configs(adb: str, serial: str) -> list[str]:
    out = _run([adb, "-s", serial, "shell", f"ls -1 {DOWNLOAD_DIR}/*.zip 2>/dev/null"]).stdout
    return [line.strip() for line in out.splitlines() if line.strip().endswith(".zip")]


def push(adb: str, serial: str, local: Path) -> bool:
    return _run([adb, "-s", serial, "push", str(local), f"{DOWNLOAD_DIR}/{local.name}"], timeout=120).returncode == 0


def clicker_package(adb: str, serial: str) -> str | None:
    out = _run([adb, "-s", serial, "shell", "pm", "list", "packages"]).stdout.split()
    return next((package for package in CLICKER_PACKAGES if f"package:{package}" in out), None)


def _backup_broadcast(adb: str, serial: str, package: str, action: str, name: str) -> str | None:
    out = _run([adb, "-s", serial, "shell", "am", "broadcast", "-a", f"com.buzbuz.smartautoclicker.{action}",
                "-n", f"{package}/{BACKUP_RECEIVER}", "--es", "file", name], timeout=120).stdout
    match = re.search(r'result=1(?:, data="(\d+)")?', out)
    return (match[1] or "0") if match else None


def export_scenarios(adb: str, serial: str, local: Path) -> bool:
    """Asks the clicker to back up every scenario it has, then copies that backup here."""
    package = clicker_package(adb, serial)
    if not package or _backup_broadcast(adb, serial, package, "EXPORT_SCENARIOS", EXPORT_FILE) is None:
        return False
    return pull(adb, serial, f"/sdcard/Android/data/{package}/files/{EXPORT_FILE}", local)


def import_scenarios(adb: str, serial: str, local: Path) -> int | None:
    """Copies a backup into the clicker's folder and has the clicker import it. Returns how many scenarios came in."""
    package = clicker_package(adb, serial)
    if not package:
        return None
    folder = f"/sdcard/Android/data/{package}/files"
    _run([adb, "-s", serial, "shell", "mkdir", "-p", folder])
    if _run([adb, "-s", serial, "push", str(local), f"{folder}/{IMPORT_FILE}"], timeout=120).returncode != 0:
        return None
    count = _backup_broadcast(adb, serial, package, "IMPORT_SCENARIOS", IMPORT_FILE)
    _run([adb, "-s", serial, "shell", "rm", "-f", f"{folder}/{IMPORT_FILE}"])
    return int(count) if count is not None else None


def pull(adb: str, serial: str, remote: str, local: Path) -> bool:
    return _run([adb, "-s", serial, "pull", remote, str(local)], timeout=120).returncode == 0
