"""User settings, adb lookup and first-time setup."""
import json
import os
import platform
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

APP_NAME = "dual-login"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_DIR = data_dir()
SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULTS = {
    "device_a": "",
    "device_b": "",
    "device_a_name": "",
    "device_b_name": "",
    "tutorial_done": False,
    "start_automatically": True,
    "write_logs": True,
    "token": "",
    "port": 8765,
    "ntfy_url": "",
    "game_package": "com.Level5.YWP",
    "login_button_x": 525,
    "login_button_y": 1187,
    "min_stage_seconds": 18.0,
    "stuck_after_seconds": 40.0,
    "alert_after_seconds": 240.0,
    "alert_repeat_seconds": 300.0,
    "force_stop_after_seconds": 300.0,
    "both_at_login_window_seconds": 6.0,
    "login_retry_seconds": 8.0,
    "stage_clear_tap_delay_seconds": 0.20,
    "tap_jitter_pixels": 8,
    "points_per_clear": 22,
    "forecast_target_datetime": "",
    "stats_interval_seconds": 5.0,
    "stats_clear_sample_size": 100,
    "stats_uptime_window_hours": 24.0,
    "restart_enabled": True,
    "restart_every_minutes": 1200,
    "restart_boot_timeout_seconds": 240,
    "rescue_restart_seconds": 600,
    "on_close": "ask",
    "dual_configs": {},
}


def load() -> dict:
    data = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        data.update(json.loads(SETTINGS_FILE.read_text()))
    return data


def save(data: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(data, indent=2) + "\n")


# ---- adb ----

_ADB_NAME = "adb.exe" if platform.system() == "Windows" else "adb"
_PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-{}.zip"


def find_adb() -> str | None:
    local = DATA_DIR / "platform-tools" / _ADB_NAME
    if local.exists():
        return str(local)
    return shutil.which("adb")


def download(url: str, target: Path) -> None:
    """Downloads with certifi's certificates, which a packaged build needs on macOS and Linux."""
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = None
    partial = target.with_name(target.name + ".part")
    with urllib.request.urlopen(url, timeout=60, context=context) as response, open(partial, "wb") as out:
        shutil.copyfileobj(response, out)
    partial.replace(target)


def download_adb() -> str:
    key = {"Windows": "windows", "Darwin": "darwin"}.get(platform.system(), "linux")
    url = _PLATFORM_TOOLS_URL.format(key)
    zip_path = DATA_DIR / "platform-tools.zip"
    print(f"Downloading Android platform-tools from {url} ...")
    download(url, zip_path)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(DATA_DIR)
    zip_path.unlink()
    adb = DATA_DIR / "platform-tools" / _ADB_NAME
    adb.chmod(0o755)
    return str(adb)


def _console_download_adb() -> bool:
    print("adb (Android platform-tools) was not found on this computer.")
    return input("Download it from Google now? [Y/n] ").strip().lower() in ("", "y", "yes")


def _console_pick_devices(devices: list[str]) -> list[str] | None:
    print(f"\nFound {len(devices)} device(s): {', '.join(devices) or 'none'}")
    print("Start BOTH BlueStacks instances and enable 'Android Debug Bridge (ADB)' in each one's settings.")
    choice = input("Press Enter to look again, or type two serials separated by a space: ").strip().split()
    return choice if len(choice) == 2 else None


def ensure_token() -> None:
    data = load()
    if not data["token"]:
        data["token"] = secrets.token_urlsafe(12)
        save(data)


def ensure_adb(confirm_download=_console_download_adb) -> str:
    adb = find_adb()
    if adb:
        return adb
    if confirm_download():
        return download_adb()
    raise SystemExit("adb is required. Install Android platform-tools and run again.")


def list_devices(adb: str) -> list[str]:
    subprocess.run([adb, "start-server"], capture_output=True, timeout=30, creationflags=NO_WINDOW)
    out = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=30,
                         creationflags=NO_WINDOW).stdout
    return [line.split()[0] for line in out.splitlines()[1:] if line.strip().endswith("device")]


def connect_local_instances(adb: str) -> None:
    """BlueStacks only shows up in `adb devices` once connected, so try the ports its instances listen on."""
    for port in list(range(5555, 5656, 10)) + list(range(5554, 5586, 2)):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                pass
        except OSError:
            continue
        subprocess.run([adb, "connect", f"127.0.0.1:{port}"], capture_output=True, timeout=30,
                       creationflags=NO_WINDOW)


# ---- first-time setup ----

def _choose_devices(adb: str, settings: dict, pick_devices) -> None:
    while True:
        devices = list_devices(adb)
        if len(devices) < 2:
            connect_local_instances(adb)
            devices = list_devices(adb)
        for _ in range(8):
            if len(devices) >= 2:
                break
            time.sleep(1)
            devices = list_devices(adb)
        if len(devices) == 2:
            settings["device_a"], settings["device_b"] = sorted(devices)
            return
        choice = pick_devices(devices)
        if choice:
            settings["device_a"], settings["device_b"] = choice
            return


def ensure_setup(confirm_download=_console_download_adb, pick_devices=_console_pick_devices) -> dict:
    """Creates settings.json on first run (finds adb and the two devices, makes a token). Returns the settings."""
    settings = load()
    adb = ensure_adb(confirm_download)
    if not settings["device_a"] or not settings["device_b"]:
        _choose_devices(adb, settings, pick_devices)
    if not settings["token"]:
        settings["token"] = secrets.token_urlsafe(12)
    save(settings)
    return settings


def print_app_instructions(settings: dict) -> None:
    port, token = settings["port"], settings["token"]
    print("\n" + "=" * 64)
    print("Smart AutoClicker setup (ADB action, on each device)")
    print("  Bridge address : 127.0.0.1 (linked through adb reverse on start)")
    print(f"  Port           : {port}")
    print(f"  Bridge token   : {token}")
    print(f"  Commands       : stage-clear {settings['device_a']}   (on the device {settings['device_a']})")
    print(f"                   stage-clear {settings['device_b']}   (on the device {settings['device_b']})")
    print(f"Settings file: {SETTINGS_FILE}")
    print("=" * 64 + "\n")
