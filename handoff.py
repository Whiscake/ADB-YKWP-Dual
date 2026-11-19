"""Keeps two BlueStacks instances of YWP in step so only one plays a stage at a time.

Smart AutoClicker on each device sends events to this server as "<event-name> <serial>":

    stage-clear     the device finished a stage. The other device is logged in and the game on this one is stopped
    login-screen    the login screen is visible; used to work out which device is idle
    <anything-else> logged only

Alerts are sent through ntfy.sh when a device stops progressing, if a topic URL is configured.

    python3 main.py
"""
import argparse
import hmac
import logging
import logging.handlers
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import adb
import config
import emulators
import notify
import settings
import stats

LOG_DIR = settings.DATA_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

log = logging.getLogger("coordinator")
log.setLevel(logging.INFO)
_formatter = logging.Formatter("%(asctime)s %(message)s")
LOG_DAYS = 3
for _old in LOG_DIR.glob("coordinator.log.[0-9]"):
    _old.unlink(missing_ok=True)
_file_handler = logging.handlers.TimedRotatingFileHandler(
    LOG_DIR / "coordinator.log", when="midnight", backupCount=LOG_DAYS, encoding="utf-8"
)
_file_handler.setFormatter(_formatter)


def set_file_logging(enabled: bool) -> None:
    if enabled:
        log.addHandler(_file_handler)
    else:
        log.removeHandler(_file_handler)


set_file_logging(config.WRITE_LOGS)

DEVICES = (config.DEVICE_A, config.DEVICE_B)
WATCHDOG_INTERVAL_SECONDS = 5.0
RELINK_EVERY_TICKS = 12
EVENT_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def _port(serial: str) -> str:
    return serial.rsplit("-", 1)[-1].rsplit(":", 1)[-1]


def resolve_serial(name: str) -> str | None:
    """Accepts a device serial, '127.0.0.1:<port>', 'emulator-<port>' or just the port number.
    BlueStacks lists an instance as 127.0.0.1:5555 while the emulator name for it is emulator-5554, one lower."""
    if name in DEVICES:
        return name
    port = _port(name)
    if not port.isdigit():
        return None
    wanted = [port] + ([str(int(port) + 1)] if name.startswith("emulator-") else [])
    for candidate in wanted:
        matches = [d for d in DEVICES if _port(d) == candidate]
        if len(matches) == 1:
            return matches[0]
    return None


def _other(serial: str) -> str:
    return config.DEVICE_B if serial == config.DEVICE_A else config.DEVICE_A


class Coordinator:
    def __init__(self, active: str | None) -> None:
        self.active = active
        self.active_since = time.time()
        self.last_login_tap = 0.0
        self.login_screen_at: dict[str, float] = {}
        self.last_rejected: tuple[str, float] = ("", 0.0)
        self.stuck_since: float | None = None
        self.last_alert_at: float | None = None
        self.last_force_stop_at: float | None = None
        self.restarting: set[str] = set()
        self.restart_requested = False
        self.restart_pending: set[str] = set()
        self.restart_wait: dict[str, tuple[str, float]] = {}
        self.last_rescue_at = 0.0
        self.last_event: dict[str, tuple[str, float]] = {}
        self.cycle_started_at = time.time()
        self.stopped = threading.Event()
        self.server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    @property
    def idle(self) -> str | None:
        return None if self.active is None else _other(self.active)

    def on_stage_clear(self, serial: str) -> str:
        """Returns a human readable outcome. Safe to call concurrently; repeats for one clear are ignored."""
        with self._lock:
            if self.active is None:
                log.info("learned active device from its first stage clear: %s", serial)
                self.active = serial
                self.active_since = time.time() - config.INITIAL_POLL_DELAY_SECONDS
            if serial != self.active:
                return f"ignored: {serial} is not the active device ({self.active})"
            if self.idle in self.restarting:
                self.active_since = time.time()
                return f"ignored: {self.idle} is restarting, {serial} keeps playing"
            elapsed = time.time() - self.active_since
            if elapsed < config.INITIAL_POLL_DELAY_SECONDS:
                return f"ignored: {serial} became active {elapsed:.1f}s ago, too soon to have cleared"

            cleared, idle = self.active, self.idle
            log.info("stage clear detected on %s", cleared)
            time.sleep(config.STAGE_CLEAR_TAP_DELAY_SECONDS)
            x, y = config.LOGIN_BUTTON_COORDS
            log.info("tapping login on %s at (%d, %d)", idle, x, y)
            adb.tap(idle, x, y)
            self.last_login_tap = time.time()
            log.info("force-stopping %s on %s to skip results animation", config.GAME_PACKAGE, cleared)
            try:
                adb.force_stop(cleared, config.GAME_PACKAGE)
            except Exception as exc:
                log.error("force-stop of %s failed, continuing handoff: %s", cleared, exc)
                notify.send(f"🚨 dual-login: could not force-stop {cleared}: {exc}", priority="urgent")

            if self.stuck_since is not None:
                log.info("recovered after being stuck for %.0fs", time.time() - self.stuck_since)
            self.stuck_since = None
            self.last_alert_at = None
            self.last_force_stop_at = None
            self.active = idle
            self.active_since = time.time()
            return f"ok: {cleared} -> {idle}"

    def on_login_screen(self, serial: str) -> str:
        """Never taps login on its own: login is only tapped after a stage clear, or when both devices are at login."""
        with self._lock:
            now = time.time()
            self.login_screen_at[serial] = now
            other = _other(serial)
            both_at_login = now - self.login_screen_at.get(other, 0.0) <= config.BOTH_AT_LOGIN_WINDOW_SECONDS

            if not both_at_login:
                if self.active is None:
                    self.active = other
                    self.active_since = now - config.INITIAL_POLL_DELAY_SECONDS
                    log.info("learned active device from %s being at login: %s", serial, self.active)
                    return "ok"
                return "ignored: only one device at login"

            if now - self.last_login_tap < config.LOGIN_RETRY_SECONDS:
                return "ignored: login tapped recently"
            chosen = self.active if self.active in DEVICES else config.DEVICE_A
            x, y = config.LOGIN_BUTTON_COORDS
            log.info("both devices are at login, tapping login on %s at (%d, %d)", chosen, x, y)
            adb.tap(chosen, x, y)
            self.active = chosen
            self.active_since = time.time()
            self.last_login_tap = time.time()
            if self.stuck_since is not None:
                log.info("recovered after being stuck for %.0fs", time.time() - self.stuck_since)
            self.stuck_since = None
            self.last_alert_at = None
            self.last_force_stop_at = None
            return "ok"

    def on_event(self, name: str, serial: str) -> str:
        log.info("event %s on %s", name, serial)
        return "ok"

    def restart_due(self) -> str | None:
        """Starts a restart cycle when it is due or asked for, then returns the idle device if it still needs one.
        Each device is restarted only while the other plays, one after the other, then the cycle ends."""
        with self._lock:
            minutes = config.RESTART_EVERY_MINUTES
            due = minutes and time.time() - self.cycle_started_at >= minutes * 60
            if not self.restart_pending and (due or self.restart_requested):
                self.restart_requested = False
                self.cycle_started_at = time.time()
                self.restart_pending = {serial for serial in DEVICES if emulators.restartable(serial)}
                skipped = set(DEVICES) - self.restart_pending
                if skipped:
                    log.warning("not restarting %s: only LDPlayer instances can be restarted", ", ".join(skipped))
                if self.restart_pending:
                    log.info("restart cycle started: each emulator restarts while the other plays")
            idle = self.idle
            if idle is None or self.restarting or idle not in self.restart_pending:
                return None
            self.restarting.add(idle)
            return idle

    def restart(self, serial: str) -> None:
        """Restarts the emulator, waits for it, links the clicker again, resumes its scenario and opens the game."""
        if self.active in self.restarting:
            log.info("restarting %s", serial)
        else:
            log.info("restarting %s to clear the lag; %s keeps playing, stuck checks paused", serial, self.active)
        try:
            package = adb.installed_clicker(serial)
            if not emulators.reboot(serial):
                raise RuntimeError("only LDPlayer instances can be restarted")
            self._wait(serial, "shutting down", lambda: not adb.booted(serial), 60, 2)
            if not self._wait(serial, "adb check", lambda: adb.ensure_connected(serial) and adb.booted(serial),
                              config.RESTART_BOOT_TIMEOUT_SECONDS):
                raise RuntimeError(f"not back after {config.RESTART_BOOT_TIMEOUT_SECONDS:.0f}s")
            emulators.sort_windows()
            if package and self._wait(serial, "clicker", lambda: adb.clicker_service_ready(serial, package), 120):
                if not self._wait(serial, "linking", lambda: link_clicker(serial, quiet=True), 120, 5):
                    log.error("could not link the clicker on %s to this computer after the restart", serial)
                adb.resume_clicker(serial, package, config.DUAL_CONFIGS.get(serial))
            else:
                log.error("the clicker accessibility service on %s did not come back; enable it by hand", serial)
            time.sleep(5)
            adb.start_game(serial, config.GAME_PACKAGE)
            emulators.sort_windows()
            log.info("%s restarted and resumed", serial)
        except Exception as exc:
            log.error("restart of %s failed: %s", serial, exc)
            notify.send(f"🚨 dual-login: restart of {serial} failed: {exc}", priority="urgent")
        finally:
            with self._lock:
                self.restart_wait.pop(serial, None)
                self.restarting.discard(serial)
                self.restart_pending.discard(serial)
                self.active_since = time.time()
                self.stuck_since = None
                if not self.restart_pending:
                    self.cycle_started_at = time.time()
                    log.info("restart cycle finished")

    def _wait(self, serial: str, phase: str, check, timeout: float, interval: float = 3.0) -> bool:
        self.restart_wait[serial] = (phase, time.time() + timeout)
        return adb.wait_until(check, timeout, interval)

    def rescue(self) -> bool:
        targets = {serial for serial in DEVICES if emulators.restartable(serial)}
        with self._lock:
            targets -= self.restarting
            self.restart_pending |= targets
            self.restarting |= targets
        if not targets:
            return False
        log.error("no stage cleared in %.0fs even after closing the game, restarting %s",
                  config.RESCUE_RESTART_SECONDS, ", ".join(sorted(targets)))
        notify.send(f"🚨 dual-login: nothing cleared in {int(config.RESCUE_RESTART_SECONDS)}s, restarting the "
                    f"emulators", priority="urgent")
        for serial in targets:
            threading.Thread(target=self.restart, args=(serial,), daemon=True).start()
        return True

    def watchdog_step(self) -> None:
        """Warns, alerts and force-stops the game on a device that stopped clearing stages. It never taps blindly."""
        to_restart = self.restart_due()
        if to_restart:
            threading.Thread(target=self.restart, args=(to_restart,), daemon=True).start()
        to_stop = None
        with self._lock:
            if self.active is None or self.restarting:
                return
            now = time.time()
            elapsed = now - self.active_since
            rescue = config.RESCUE_RESTART_SECONDS and elapsed > config.RESCUE_RESTART_SECONDS and                 now - self.last_rescue_at > config.RESCUE_RESTART_SECONDS
            if rescue:
                self.last_rescue_at = now
        if rescue and self.rescue():
            return
        with self._lock:
            if elapsed <= config.STUCK_TIMEOUT_SECONDS:
                return
            now = time.time()
            if self.stuck_since is None:
                self.stuck_since = now
                log.warning("%s has not cleared a stage in %.0fs, assuming it's stuck", self.active, elapsed)
            stuck_elapsed = elapsed
            if stuck_elapsed > config.ESCALATION_TIMEOUT_SECONDS and (
                self.last_alert_at is None or now - self.last_alert_at > config.ALERT_REPEAT_SECONDS
            ):
                log.error("%s still stuck after %.0fs, sending alert", self.active, stuck_elapsed)
                notify.send(
                    f"🚨 dual-login stuck: {self.active} has not cleared a stage in {int(stuck_elapsed)}s",
                    priority="urgent",
                )
                self.last_alert_at = now
            if config.FORCE_STOP_AFTER_SECONDS > 0 and elapsed > config.FORCE_STOP_AFTER_SECONDS and (
                self.last_force_stop_at is None or now - self.last_force_stop_at > config.FORCE_STOP_AFTER_SECONDS
            ):
                self.last_force_stop_at = now
                to_stop = self.active
        if to_stop:  # outside the lock: adb can take seconds and events shouldn't wait on it
            log.error("%s still stuck after %.0fs, force-stopping %s; play resumes when both devices reach login",
                      to_stop, elapsed, config.GAME_PACKAGE)
            try:
                adb.force_stop(to_stop, config.GAME_PACKAGE)
            except Exception as exc:
                log.error("force-stop of %s failed: %s", to_stop, exc)
                notify.send(f"🚨 dual-login: could not force-stop {to_stop}: {exc}", priority="urgent")


def check_device(serial: str) -> str | None:
    """Why `serial` can't play, or None when its clicker has a config to play."""
    try:
        adb.ensure_connected(serial)
        package = adb.installed_clicker(serial)
        if not package:
            return f"the clicker is not installed on {serial}"
        if not adb.clicker_service_ready(serial, package):
            return f"the clicker accessibility service on {serial} is off: turn it on in the Android settings"
        if not config.DUAL_CONFIGS.get(serial) and not adb.clicker_playing(serial, package):
            return f"no config is running on {serial}: pick one next to {serial} on the panel, or start it in the clicker"
    except Exception as exc:
        return f"could not check {serial}: {exc}"
    return None


def prepare_device(serial: str) -> None:
    """Plays the chosen config, or the one already open, then opens the game if it is closed."""
    try:
        package = adb.installed_clicker(serial)
        scenario = config.DUAL_CONFIGS.get(serial)
        adb.resume_clicker(serial, package, scenario)
        log.info("%s plays %s", serial, scenario or "its running config")
        if not adb.game_running(serial, config.GAME_PACKAGE):
            time.sleep(3)
            adb.start_game(serial, config.GAME_PACKAGE)
            log.info("opened the game on %s", serial)
    except Exception as exc:
        log.error("could not get %s ready: %s", serial, exc)


def link_clicker(serial: str, force: bool = True, quiet: bool = False) -> bool:
    """Points the clicker on `serial` at this computer. Without `force` it only acts when the adb reverse link is gone,
    which is what an emulator restart does. True when the clicker is linked."""
    try:
        if not force and adb.is_reversed(serial, config.BRIDGE_PORT):
            return True
        adb.reverse(serial, config.BRIDGE_PORT)
        adb.push_bridge(serial, config.BRIDGE_PORT, config.BRIDGE_TOKEN)
        log.info("%s linked to this computer through adb reverse on port %d", serial, config.BRIDGE_PORT)
        return True
    except Exception as exc:
        if not quiet:
            log.error("could not link the clicker on %s to this computer: %s", serial, exc)
        return False


def watchdog_loop(coordinator: Coordinator) -> None:
    down: set[str] = set()  # devices adb can't reach, so a persistent outage is logged once, not every tick
    ticks = 0
    while not coordinator.stopped.is_set():
        ticks += 1
        try:
            coordinator.watchdog_step()
        except Exception as exc:
            log.error("watchdog error: %s", exc, exc_info=True)
        try:
            # adb forgets TCP devices when its server restarts or the link drops; nothing else would reconnect them
            for serial, ok in adb.reconnect_missing(set(DEVICES) - coordinator.restarting).items():
                if ok:
                    down.discard(serial)
                    log.warning("%s had dropped off adb, reconnected", serial)
                    link_clicker(serial)
                elif serial not in down:
                    down.add(serial)
                    log.error("%s is not attached to adb and reconnecting failed, retrying every %.0fs",
                              serial, WATCHDOG_INTERVAL_SECONDS)
        except Exception as exc:
            log.error("adb reconnect check failed: %s", exc)
        if ticks % RELINK_EVERY_TICKS == 0:
            for serial in set(DEVICES) - down - coordinator.restarting:
                link_clicker(serial, force=False)
        coordinator.stopped.wait(WATCHDOG_INTERVAL_SECONDS)


def make_handler(coordinator: Coordinator):
    class Handler(BaseHTTPRequestHandler):
        timeout = 10

        def _reply(self, code: int, text: str) -> None:
            data = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path != "/health":
                return self._reply(404, "not found")
            self._reply(200, f"ok active={coordinator.active}")

        def do_POST(self) -> None:
            if self.path != "/adb":
                return self._reply(404, "not found")
            length = int(self.headers.get("Content-Length", 0))
            if length > 1024:
                return self._reply(413, "too large")
            body = self.rfile.read(length).decode()
            raw = parse_qs(body).get("command", [""])[0]
            given = self.headers.get("X-Adb-Bridge-Token", "")
            if not hmac.compare_digest(given.encode(), config.BRIDGE_TOKEN.encode()):
                if time.time() - coordinator.last_rejected[1] > 60:
                    coordinator.last_rejected = ("", time.time())
                    log.warning("rejected %r: wrong bridge token (got %d characters, expected %d)",
                                raw, len(given), len(config.BRIDGE_TOKEN))
                return self._reply(401, "bad token")
            command = raw.split()
            if len(command) == 2:
                command[1] = resolve_serial(command[1]) or command[1]
            if len(command) != 2 or not EVENT_NAME.fullmatch(command[0]) or command[1] not in DEVICES:
                if raw != coordinator.last_rejected[0] or time.time() - coordinator.last_rejected[1] > 60:
                    coordinator.last_rejected = (raw, time.time())
                    log.warning("rejected command %r from %s: expected '<event-name> <%s>'",
                                raw, self.client_address[0], "|".join(DEVICES))
                return self._reply(400, f"expected '<event-name> <{'|'.join(DEVICES)}>'")

            threading.Thread(target=self._handle, args=(command[0], command[1]), daemon=True).start()
            self._reply(200, "accepted")

        @staticmethod
        def _handle(name: str, serial: str) -> None:
            coordinator.last_event[serial] = (name, time.time())
            try:
                if name == "stage-clear":
                    outcome = coordinator.on_stage_clear(serial)
                elif name == "login-screen":
                    outcome = coordinator.on_login_screen(serial)
                else:
                    outcome = coordinator.on_event(name, serial)
            except Exception as exc:
                log.error("handling %s on %s failed: %s", name, serial, exc, exc_info=True)
                notify.send(f"🚨 dual-login error: {exc}", priority="urgent")

        def log_message(self, *_) -> None:
            pass

    return Handler


def start(active: str | None = None) -> Coordinator:
    """Attaches both devices, then runs the watchdog and the event server in background threads."""
    global DEVICES
    config.reload()
    if not config.BRIDGE_TOKEN:
        raise SystemExit("No bridge token: run main.py (or the app) so first-run setup can create one.")
    DEVICES = (config.DEVICE_A, config.DEVICE_B)
    set_file_logging(config.WRITE_LOGS)

    coordinator = Coordinator(active)
    server = ThreadingHTTPServer((config.BRIDGE_HOST, config.BRIDGE_PORT), make_handler(coordinator))
    coordinator.server = server
    adb.restart_server()
    for serial in DEVICES:
        if adb.ensure_connected(serial):
            log.info("%s attached to adb", serial)
            link_clicker(serial)
        else:
            log.error("%s could not be attached to adb; the watchdog keeps retrying", serial)

    coordinator.active_since = time.time() - config.INITIAL_POLL_DELAY_SECONDS
    log.info("starting: active=%s idle=%s (event-driven, listening on %s:%d)",
             coordinator.active, coordinator.idle, config.BRIDGE_HOST, config.BRIDGE_PORT)

    threading.Thread(target=watchdog_loop, args=(coordinator,), daemon=True).start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return coordinator


def stop(coordinator: Coordinator) -> None:
    coordinator.stopped.set()
    if coordinator.server:
        coordinator.server.shutdown()
        coordinator.server.server_close()
    log.info("stopped")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--active", choices=DEVICES, default=None,
                    help="device currently playing a stage. Optional: left out, it is learned from the first "
                         "stage-clear event, or from a login-screen event on the idle device")
    ap.add_argument("--log", action="store_true",
                    help="print the full scrolling log instead of the live stats screen")
    ap.add_argument("--console", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    coordinator = start(args.active)
    if args.log:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(_formatter)
        log.addHandler(console_handler)
    else:
        stats.start_screen(coordinator)
    while True:
        time.sleep(3600)

if __name__ == "__main__":
    main()
