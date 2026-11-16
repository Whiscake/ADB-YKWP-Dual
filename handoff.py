"""Keeps two BlueStacks instances of YWP in step so only one plays a stage at a time.

Smart AutoClicker on each device sends events to this server as "<event-name> <serial>":

    stage-clear     the device finished a stage. The other device is logged in and the game on this one is stopped
    login-screen    the login screen is visible; used to work out which device is idle
    login-success   the device finished logging in
    hold            the device needs a person; pauses stuck detection and optionally sends an alert
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
import notify
import settings

LOG_DIR = settings.DATA_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

log = logging.getLogger("coordinator")
log.setLevel(logging.INFO)
_formatter = logging.Formatter("%(asctime)s %(message)s")
for _handler in (
    logging.StreamHandler(),
    logging.handlers.RotatingFileHandler(LOG_DIR / "coordinator.log", maxBytes=5 * 1024 * 1024, backupCount=5),
):
    _handler.setFormatter(_formatter)
    log.addHandler(_handler)

DEVICES = (config.DEVICE_A, config.DEVICE_B)
WATCHDOG_INTERVAL_SECONDS = 5.0
EVENT_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def _other(serial: str) -> str:
    return config.DEVICE_B if serial == config.DEVICE_A else config.DEVICE_A


class Coordinator:
    def __init__(self, active: str | None) -> None:
        self.active = active
        self.active_since = time.time()
        self.login_pending = False
        self.last_login_tap = 0.0
        self.login_screen_at: dict[str, float] = {}
        self.last_rejected: tuple[str, float] = ("", 0.0)
        self.stuck_since: float | None = None
        self.last_hold_at = 0.0
        self.last_hold_alert_at = 0.0
        self.last_alert_at: float | None = None
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
            elapsed = time.time() - self.active_since
            if elapsed < config.INITIAL_POLL_DELAY_SECONDS:
                return f"ignored: {serial} became active {elapsed:.1f}s ago, too soon to have cleared"

            cleared, idle = self.active, self.idle
            log.info("stage clear detected on %s", cleared)
            time.sleep(config.STAGE_CLEAR_TAP_DELAY_SECONDS)
            x, y = config.LOGIN_BUTTON_COORDS
            log.info("tapping login on %s at (%d, %d)", idle, x, y)
            adb.tap(idle, x, y)
            self.login_pending = True
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
            self.active = idle
            self.active_since = time.time()
            return f"ok: {cleared} -> {idle}"

    def on_login_success(self, serial: str) -> str:
        with self._lock:
            if serial != self.active or not self.login_pending:
                return f"ignored: no login pending on {serial}"
            log.info("login confirmed on %s after %.1fs", serial, time.time() - self.last_login_tap)
            self.login_pending = False
            return "ok"

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
            self.login_pending = True
            self.last_login_tap = time.time()
            return "ok"

    def on_hold(self, serial: str) -> str:
        with self._lock:
            if serial != self.active:
                return f"ignored: {serial} is not the active device ({self.active})"
            now = time.time()
            if now - self.last_hold_at > config.HOLD_GAP_SECONDS:
                log.warning("%s reports it needs a person, holding stuck detection", serial)
            self.last_hold_at = now
            self.active_since = now
            self.stuck_since = None
            self.last_alert_at = None
            if config.HOLD_NOTIFY and now - self.last_hold_alert_at > config.ALERT_REPEAT_SECONDS:
                self.last_hold_alert_at = now
                notify.send(f"🧑 dual-login: {serial} needs you (not treated as stuck)", priority="urgent")
            return "ok"

    def on_event(self, name: str, serial: str) -> str:
        log.info("event %s on %s", name, serial)
        return "ok"

    def watchdog_step(self) -> None:
        """Alert-only: without screenshots we can't tell where a stuck device is, so never tap blindly."""
        with self._lock:
            if self.active is None:
                return
            elapsed = time.time() - self.active_since
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


def watchdog_loop(coordinator: Coordinator) -> None:
    while True:
        try:
            coordinator.watchdog_step()
        except Exception as exc:
            log.error("watchdog error: %s", exc, exc_info=True)
        time.sleep(WATCHDOG_INTERVAL_SECONDS)


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
            if len(command) == 2 and command[1].isdigit():
                command[1] = f"emulator-{command[1]}"
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
            try:
                if name == "stage-clear":
                    outcome = coordinator.on_stage_clear(serial)
                elif name == "login-success":
                    outcome = coordinator.on_login_success(serial)
                elif name == "hold":
                    outcome = coordinator.on_hold(serial)
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--active", choices=DEVICES, default=None,
                    help="device currently playing a stage. Optional: left out, it is learned from the first "
                         "stage-clear event, or from a login-screen event on the idle device")
    args = ap.parse_args()
    if not config.BRIDGE_TOKEN:
        raise SystemExit("No bridge token: run main.py (or the app) so first-run setup can create one.")

    adb.restart_server()
    for serial in DEVICES:
        adb.connect(serial)

    coordinator = Coordinator(args.active)
    coordinator.active_since = time.time() - config.INITIAL_POLL_DELAY_SECONDS
    log.info("starting: active=%s idle=%s (event-driven, listening on %s:%d)",
             coordinator.active, coordinator.idle, config.BRIDGE_HOST, config.BRIDGE_PORT)

    threading.Thread(target=watchdog_loop, args=(coordinator,), daemon=True).start()
    ThreadingHTTPServer((config.BRIDGE_HOST, config.BRIDGE_PORT), make_handler(coordinator)).serve_forever()


if __name__ == "__main__":
    main()
