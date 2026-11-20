"""Single mode: every room plays on its own; the program only checks that each one keeps clearing battles.

The clicker of each room sends "stage-clear <serial>" at the end of every battle. A room that stops sending it gets
the game closed and opened again, then a restart of its emulator (LDPlayer only).
"""
import threading
import time

import adb
import config
import emulators
import handoff
import notify
import stats

log = handoff.log


class Solo:
    def __init__(self, rooms: tuple[str, ...]) -> None:
        now = time.time()
        self.rooms = rooms
        self.active = None
        self.idle = None
        self.last_clear = {serial: now - config.INITIAL_POLL_DELAY_SECONDS for serial in rooms}
        self.cycle_at = dict.fromkeys(rooms, now)
        self.stuck_since: dict[str, float] = {}
        self.last_alert_at: dict[str, float] = {}
        self.last_reopen_at: dict[str, float] = {}
        self.last_rescue_at: dict[str, float] = {}
        self.reopening: set[str] = set()
        self.cleared: set[str] = set()
        self.restarting: set[str] = set()
        self.restart_pending: set[str] = set()
        self.restart_requested = False
        self.restart_wait: dict[str, tuple[str, float]] = {}
        self.last_event: dict[str, tuple[str, float]] = {}
        self.last_rejected: tuple[str, float] = ("", 0.0)
        self.stopped = threading.Event()
        self.server = None
        self._lock = threading.Lock()

    def status(self) -> str:
        now = time.time()
        stuck = [serial for serial in self.rooms if serial in self.stuck_since]
        if stuck:
            return "STUCK - no clear: " + ", ".join(f"{serial} {stats._duration(now - self.last_clear[serial])}"
                                                    for serial in stuck)
        playing = len(self.rooms) - len(self.restarting)
        return f"OK - {playing} room{'' if playing == 1 else 's'} playing"

    def on_stage_clear(self, serial: str, received: float | None = None) -> str:
        with self._lock:
            now = time.time()
            if serial in self.restarting:
                return f"ignored: {serial} is restarting"
            if now - self.last_clear[serial] < config.INITIAL_POLL_DELAY_SECONDS:
                return "ignored: too soon after the last clear"
            log.info("stage clear detected on %s", serial)
            since = self.stuck_since.pop(serial, None)
            if since is not None:
                log.info("%s recovered after being stuck for %.0fs", serial, now - since)
            self.last_clear[serial] = now
            self.cleared.add(serial)
            self.last_alert_at.pop(serial, None)
            self.last_reopen_at.pop(serial, None)
            return "ok"

    def on_login_screen(self, serial: str) -> str:
        return "ignored: single mode"

    def on_event(self, name: str, serial: str) -> str:
        return "ok"

    def restart_room(self, serial: str) -> bool:
        with self._lock:
            if serial in self.restarting or not emulators.restartable(serial):
                return False
            self.restarting.add(serial)
        threading.Thread(target=self.restart, args=(serial,), daemon=True).start()
        return True

    def restart(self, serial: str) -> None:
        log.info("restarting %s", serial)
        try:
            handoff.reboot_and_resume(serial, self.restart_wait)
        finally:
            with self._lock:
                now = time.time()
                self.restarting.discard(serial)
                self.restart_pending.discard(serial)
                self.cycle_at[serial] = now
                self.last_clear[serial] = now
                self.cleared.discard(serial)
                self.last_reopen_at.pop(serial, None)
                if self.stuck_since.pop(serial, None) is not None:
                    log.info("%s recovered after being stuck for a restart", serial)

    def _reopen(self, serial: str) -> None:
        try:
            adb.force_stop(serial, config.GAME_PACKAGE)
            time.sleep(3)
            adb.start_game(serial, config.GAME_PACKAGE)
        except Exception as exc:
            log.error("could not close and open the game on %s: %s", serial, exc)
        finally:
            self.reopening.discard(serial)

    def watchdog_step(self) -> None:
        now = time.time()
        minutes = config.RESTART_EVERY_MINUTES
        with self._lock:
            if self.restart_requested:
                self.restart_requested = False
                self.restart_pending |= {serial for serial in self.rooms if emulators.restartable(serial)}
            if minutes:
                self.restart_pending |= {serial for serial in self.rooms if now - self.cycle_at[serial] >= minutes * 60
                                         and emulators.restartable(serial)}
            due = sorted(self.restart_pending - self.restarting)
        if due and not self.restarting:
            self.restart_room(due[0])
        for serial in self.rooms:
            if serial not in self.restarting and serial not in self.reopening:
                self._check(serial, now)

    def _check(self, serial: str, now: float) -> None:
        elapsed = now - self.last_clear[serial]
        rescue = config.RESCUE_RESTART_SECONDS
        if rescue and elapsed > rescue and now - self.last_rescue_at.get(serial, 0.0) > rescue \
                and emulators.restartable(serial):
            self.last_rescue_at[serial] = now
            log.error("%s cleared nothing in %.0fs even after closing the game, restarting it", serial, elapsed)
            notify.send(f"🚨 dual-login: {serial} cleared nothing in {int(elapsed)}s, restarting it", priority="urgent")
            self.restart_room(serial)
            return
        if elapsed <= config.STUCK_TIMEOUT_SECONDS:
            return
        if serial not in self.stuck_since:
            self.stuck_since[serial] = now
            log.warning("%s has not cleared a stage in %.0fs, assuming it's stuck", serial, elapsed)
        if elapsed > config.ESCALATION_TIMEOUT_SECONDS and \
                now - self.last_alert_at.get(serial, 0.0) > config.ALERT_REPEAT_SECONDS:
            self.last_alert_at[serial] = now
            notify.send(f"🚨 dual-login stuck: {serial} has not cleared a stage in {int(elapsed)}s", priority="urgent")
        reopen = config.FORCE_STOP_AFTER_SECONDS
        if reopen and elapsed > reopen and now - self.last_reopen_at.get(serial, 0.0) > reopen:
            self.last_reopen_at[serial] = now
            self.reopening.add(serial)
            log.error("%s still stuck after %.0fs, closing and opening the game again", serial, elapsed)
            threading.Thread(target=self._reopen, args=(serial,), daemon=True).start()
