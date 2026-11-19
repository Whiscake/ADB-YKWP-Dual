"""Live status screen: current state, clear times, uptime and points, computed from the log."""
import atexit
import logging
import os
import re
import shutil
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta

import config
import settings

LOG_DIR = settings.DATA_DIR / "logs"
LOG_BASENAME = "coordinator.log"

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (.*)$")
CLEAR_MARKER = "stage clear detected on"
STUCK_MARKER = "has not cleared a stage in"
RECOVERED_MARKER = "recovered after being stuck for"
STARTING_MARKER = "starting: active="
RESTARTED_MARKER = " restarted and resumed"
RULE = "-" * 66


def _log_files() -> list:
    backups = sorted(LOG_DIR.glob(f"{LOG_BASENAME}.*"), key=lambda p: p.stat().st_mtime)
    base = LOG_DIR / LOG_BASENAME
    return backups + ([base] if base.exists() else [])


def _iter_lines():
    for path in _log_files():
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                match = LINE_RE.match(line)
                if match:
                    yield datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S,%f"), match.group(2).rstrip("\n")


def _parse_target(text: str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


def compute_stats() -> dict:
    now = datetime.now()
    window_start = now - timedelta(hours=config.STATS_UPTIME_WINDOW_HOURS)

    clear_durations: list = []
    prev_clear_ts = None
    saw_stuck_since_last_clear = False
    stuck_open_since = None
    downtime = timedelta()
    session_clears = 0
    restarts: dict[str, int] = {}

    for ts, msg in _iter_lines():
        if msg.endswith(RESTARTED_MARKER):
            if ts > window_start:
                serial = msg[:-len(RESTARTED_MARKER)]
                restarts[serial] = restarts.get(serial, 0) + 1
            continue

        if STARTING_MARKER in msg:
            stuck_open_since = None
            prev_clear_ts = None
            saw_stuck_since_last_clear = False
            session_clears = 0
            continue

        if msg.startswith(CLEAR_MARKER):
            if prev_clear_ts is not None and not saw_stuck_since_last_clear:
                clear_durations.append((ts - prev_clear_ts).total_seconds())
            prev_clear_ts = ts
            saw_stuck_since_last_clear = False
            session_clears += 1
            continue

        if STUCK_MARKER in msg:
            saw_stuck_since_last_clear = True
            if stuck_open_since is None:
                stuck_open_since = ts
            continue

        if RECOVERED_MARKER in msg:
            if stuck_open_since is not None and ts > window_start:
                downtime += ts - max(stuck_open_since, window_start)
            stuck_open_since = None

    if stuck_open_since is not None:
        downtime += now - max(stuck_open_since, window_start)

    recent = clear_durations[-config.STATS_CLEAR_SAMPLE_SIZE:]
    window_seconds = config.STATS_UPTIME_WINDOW_HOURS * 3600
    downtime_seconds = min(downtime.total_seconds(), window_seconds)
    avg_clear = sum(recent) / len(recent) if recent else None
    points_per_hour = config.POINTS_PER_CLEAR / avg_clear * 3600 if avg_clear else None

    points_to_target = None
    target = _parse_target(config.FORECAST_TARGET_DATETIME) if config.FORECAST_TARGET_DATETIME else None
    if target and points_per_hour:
        points_to_target = points_per_hour * max(0.0, (target - now).total_seconds() / 3600)

    return {
        "sample_size": len(recent),
        "avg_clear": avg_clear,
        "fastest_clear": min(recent) if recent else None,
        "slowest_clear": max(recent) if recent else None,
        "uptime_pct": max(0.0, (window_seconds - downtime_seconds) / window_seconds * 100),
        "downtime_seconds": downtime_seconds,
        "session_clears": session_clears,
        "last_clear": prev_clear_ts,
        "points_per_hour": points_per_hour,
        "points_per_day": points_per_hour * 24 if points_per_hour else None,
        "points_to_target": points_to_target,
        "target": target,
        "restarts": restarts,
    }


def _duration(seconds: float) -> str:
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes}m{secs}s"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"


def _status(coordinator) -> str:
    now = time.time()
    if coordinator.active is None:
        return "waiting for the first stage clear or login screen"
    if coordinator.stuck_since is not None:
        return f"STUCK - {coordinator.active} no clear for {_duration(now - coordinator.active_since)}"
    return f"OK - {coordinator.active} playing for {_duration(now - coordinator.active_since)}"


class RecentWarnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.lines: deque = deque(maxlen=3)
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record).splitlines()[0])


def render(coordinator, recent: RecentWarnings) -> list:
    stats = compute_stats()
    lines = [
        f"ADB-YKWP-Dual{datetime.now().strftime('%Y-%m-%d %H:%M:%S'):>53}",
        RULE,
        f"Bridge address : 127.0.0.1 (through adb reverse)   Port: {config.BRIDGE_PORT}",
        f"Bridge token   : {config.BRIDGE_TOKEN}",
        f"Devices        : {config.DEVICE_A}, {config.DEVICE_B}",
        f"Status         : {_status(coordinator)}",
        RULE,
    ]
    last = f"last clear {_duration((datetime.now() - stats['last_clear']).total_seconds())} ago" \
        if stats["last_clear"] else "no clears yet"
    lines.append(f"This run       : {stats['session_clears']} clears | "
                 f"~{stats['session_clears'] * config.POINTS_PER_CLEAR} points | {last}")
    if stats["sample_size"]:
        lines.append(f"Clear time     : avg {stats['avg_clear']:.1f}s | fastest {stats['fastest_clear']:.1f}s | "
                     f"slowest {stats['slowest_clear']:.1f}s (last {stats['sample_size']})")
    else:
        lines.append("Clear time     : no data yet")
    lines.append(f"Uptime ({int(config.STATS_UPTIME_WINDOW_HOURS)}h)   : {stats['uptime_pct']:.1f}% | "
                 f"downtime {_duration(stats['downtime_seconds'])}")
    if stats["points_per_hour"] is not None:
        points = f"Points         : ~{stats['points_per_hour']:.0f}/hour | ~{stats['points_per_day']:.0f}/24h"
        if stats["points_to_target"] is not None:
            points += f" | by {stats['target']:%Y-%m-%d %H:%M}: ~{stats['points_to_target']:.0f}"
        lines.append(points)
    else:
        lines.append("Points         : no data yet")
    lines.append(RULE)
    lines.append("Recent warnings:" + ("" if recent.lines else " none"))
    lines.extend(f"  {line}" for line in recent.lines)
    lines.append(RULE)
    lines.append(f"Settings: {settings.SETTINGS_FILE}")
    lines.append(f"Log     : {LOG_DIR / LOG_BASENAME}")
    lines.append("Ctrl+C to stop. Run with --log for the full scrolling log instead of this screen.")
    return lines


def _screen_loop(coordinator, recent: RecentWarnings) -> None:
    tty = sys.stdout.isatty()
    if tty:
        if os.name == "nt":
            os.system("")
        sys.stdout.write("\033[2J\033[?25l")
        atexit.register(lambda: sys.stdout.write("\033[?25h\n"))
    while True:
        try:
            lines = render(coordinator, recent)
        except Exception as exc:
            lines = [f"stats error: {exc}"]
        if tty:
            width = max(20, shutil.get_terminal_size().columns - 1)
            sys.stdout.write("\033[H" + "".join(line[:width] + "\033[K\n" for line in lines) + "\033[J")
        else:
            print("\n".join(lines) + "\n", flush=True)
        sys.stdout.flush()
        time.sleep(config.STATS_INTERVAL_SECONDS)


def start_screen(coordinator) -> None:
    recent = RecentWarnings()
    logging.getLogger("coordinator").addHandler(recent)
    threading.Thread(target=_screen_loop, args=(coordinator, recent), daemon=True).start()
