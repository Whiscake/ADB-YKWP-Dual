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
STARTING_MARKER = "starting: "
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
    """Clear times are measured per room in single mode, where every room plays its own battles."""
    now = datetime.now()
    window_start = now - timedelta(hours=config.STATS_UPTIME_WINDOW_HOURS)
    single = config.MODE == "single"

    durations: dict[str, list] = {}
    prev_clear: dict[str, datetime] = {}
    stuck_seen: set[str] = set()
    stuck_open: dict[str, datetime] = {}
    downtime = timedelta()
    room_downtime: dict[str, timedelta] = {}
    room_clears: dict[str, int] = {}
    room_last: dict[str, datetime] = {}
    session_clears = 0
    last_clear = None
    run_start = None
    restarts: dict[str, int] = {}

    def room(text: str) -> str:
        return text.strip() if single else ""

    for ts, msg in _iter_lines():
        if msg.endswith(RESTARTED_MARKER):
            if ts > window_start:
                serial = msg[:-len(RESTARTED_MARKER)]
                restarts[serial] = restarts.get(serial, 0) + 1
            continue

        if msg.startswith(STARTING_MARKER):
            durations.clear()
            prev_clear.clear()
            stuck_seen.clear()
            stuck_open.clear()
            room_clears.clear()
            room_downtime.clear()
            room_last.clear()
            downtime = timedelta()
            session_clears = 0
            last_clear = None
            run_start = ts
            continue

        if msg.startswith(CLEAR_MARKER):
            key = room(msg[len(CLEAR_MARKER):])
            if key in prev_clear and key not in stuck_seen:
                durations.setdefault(key, []).append((ts - prev_clear[key]).total_seconds())
            prev_clear[key] = ts
            stuck_seen.discard(key)
            session_clears += 1
            room_clears[key] = room_clears.get(key, 0) + 1
            room_last[key] = ts
            last_clear = ts
            continue

        if STUCK_MARKER in msg:
            key = room(msg.split(STUCK_MARKER)[0].removesuffix(" has"))
            stuck_seen.add(key)
            stuck_open.setdefault(key, ts)
            continue

        if RECOVERED_MARKER in msg:
            key = room(msg.split(RECOVERED_MARKER)[0])
            since = stuck_open.pop(key, None)
            if since is not None and ts > window_start:
                lost = ts - max(since, window_start)
                downtime += lost
                room_downtime[key] = room_downtime.get(key, timedelta()) + lost

    for key, since in stuck_open.items():
        lost = now - max(since, window_start)
        downtime += lost
        room_downtime[key] = room_downtime.get(key, timedelta()) + lost

    # uptime covers the run since Start, at most the uptime window
    window_seconds = config.STATS_UPTIME_WINDOW_HOURS * 3600
    if run_start is not None:
        window_seconds = max(1.0, min(window_seconds, (now - run_start).total_seconds()))
    target = _parse_target(config.FORECAST_TARGET_DATETIME) if config.FORECAST_TARGET_DATETIME else None
    hours_to_target = max(0.0, (target - now).total_seconds() / 3600) if target else None

    def group(key: str, points: float) -> dict:
        """The same numbers for the pair in dual mode (key "") and for each room in single mode."""
        values = durations.get(key, [])[-config.STATS_CLEAR_SAMPLE_SIZE:]
        avg = sum(values) / len(values) if values else None
        per_hour = points / avg * 3600 if avg else None
        lost = min(room_downtime.get(key, timedelta()).total_seconds(), window_seconds)
        return {
            "clears": room_clears.get(key, 0),
            "points": room_clears.get(key, 0) * points,
            "sample_size": len(values),
            "avg_clear": avg,
            "fastest_clear": min(values) if values else None,
            "slowest_clear": max(values) if values else None,
            "last_clear": room_last.get(key),
            "points_per_hour": per_hour,
            "points_per_day": per_hour * 24 if per_hour else None,
            "points_to_target": per_hour * hours_to_target if per_hour and hours_to_target is not None else None,
            "downtime_seconds": lost,
            "uptime_pct": (window_seconds - lost) / window_seconds * 100,
        }

    keys = [serial for serial in config.DEVICES if serial] if single else [""]
    groups = {key: group(key, config.ROOM_POINTS.get(key, config.POINTS_PER_CLEAR) if key
                         else config.POINTS_PER_CLEAR) for key in keys}
    pair = groups.get("") or group("", config.POINTS_PER_CLEAR)
    return {
        **pair,
        "session_clears": session_clears,
        "last_clear": last_clear,
        "uptime_pct": pair["uptime_pct"] if not single else
        sum(g["uptime_pct"] for g in groups.values()) / max(1, len(groups)),
        "downtime_seconds": min(downtime.total_seconds(), window_seconds * max(1, len(keys))),
        "target": target,
        "restarts": restarts,
        "run_start": run_start,
        "groups": groups,
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
    if hasattr(coordinator, "status"):
        return coordinator.status()
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
        f"Devices        : {', '.join(config.DEVICES)}",
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
