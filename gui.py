"""Control panel: one window whose pages are the tutorial, the setup screens and the live panel."""
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import adb
import config
import devices
import emulators
import handoff
import options
import prepare
import settings
import stats
import ui

LOG_LINES = 300
STATUS_COLORS = {"OK": ui.GREEN_DARK, "STUCK": ui.RED, "Restarting": "#00668A"}
IMAGES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "images"
ICON = IMAGES / "panel.ico"
RUNNING = "Config already running"


class QueueHandler(logging.Handler):
    def __init__(self, target: queue.Queue) -> None:
        super().__init__(level=logging.INFO)
        self.target = target
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.target.put((record.levelno, self.format(record).splitlines()[0]))


class WelcomePage(ui.Page):
    title = "Welcome"
    help = "This program makes two emulators take turns playing, so one is always logging in while the other plays."

    def __init__(self, app) -> None:
        super().__init__(app)
        box = tk.Frame(self, bg=ui.BACKGROUND)
        box.place(relx=0.5, rely=0.42, anchor="center")
        tk.Label(box, text="First time here?", bg=ui.BACKGROUND, fg=ui.TEXT, font=(ui.FONT, 16, "bold")).pack()
        tk.Label(box, text="The tutorial walks you through the setup in three short steps:\n"
                           "the emulators, the clicker config and the extra settings.",
                 bg=ui.BACKGROUND, fg=ui.MUTED, font=(ui.FONT, 10), justify="center").pack(pady=(8, 20))
        buttons = tk.Frame(box, bg=ui.BACKGROUND)
        buttons.pack()
        ttk.Button(buttons, text="Start tutorial", style="Big.TButton", command=app.start_tutorial).pack(
            side="left", padx=6)
        ttk.Button(buttons, text="Skip tutorial", command=app.finish_tutorial).pack(side="left", padx=6)


class PanelPage(ui.Page):
    title = "ADB-YKWP-Dual"
    help = "Press Start to begin."

    def __init__(self, app) -> None:
        super().__init__(app)
        toolbar = tk.Frame(self, bg=ui.BACKGROUND)
        toolbar.pack(fill="x")
        self.start = tk.Button(toolbar, text="▶  Start", width=10, font=(ui.FONT, 11, "bold"), fg="white",
                               bg=ui.GREEN_DARK, activebackground=ui.GREEN, activeforeground="white", relief="flat",
                               cursor="hand2", command=app.toggle)
        self.start.pack(side="left", ipady=4)
        for symbol, tip, command in (("?", "Tutorial", app.start_tutorial),
                                     ("⚙", "Settings", lambda: app.show(app.settings_page)),
                                     ("✎", "Clicker config", lambda: app.show(app.config_page)),
                                     ("⧉", "Emulators", lambda: app.show(app.emulators_page))):
            ui.icon_button(toolbar, symbol, tip, command).pack(side="right", padx=(6, 0))

        top = tk.Frame(self, bg=ui.BACKGROUND)
        top.pack(fill="x", pady=(10, 0))
        top.columnconfigure((0, 1), weight=1, uniform="cards")
        bridge = ui.Card(top, "Bridge")
        bridge.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        bridge.row("Address", "127.0.0.1", copy=True)
        self.port = bridge.row("Port", copy=True)
        self.token = bridge.row("Token", copy=True)
        self.boxes: dict[str, ttk.Combobox] = {}
        self.names: dict[str, list[str]] = {}
        self.rooms = tk.Frame(bridge.body, bg=ui.CARD)
        self.rooms.pack(fill="x", pady=(6, 0))
        self.room_rows: dict[str, tk.StringVar] = {}

        numbers = ui.Card(top, "Stats")
        numbers.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.run = numbers.row("This run")
        self.clear = numbers.row("Clear time")
        self.uptime = numbers.row("Uptime")
        self.downtime = "0s"
        self.points = numbers.row("Points", right=self._points_per_clear)
        self.target = numbers.row("Forecast")

        log_card = ui.Card(self, "Log")
        log_card.pack(fill="both", expand=True, pady=(10, 0))
        self.log = tk.Text(log_card.body, height=6, bg="#FCFCFC", fg=ui.TEXT, relief="flat", font=("Consolas", 9),
                           state="disabled", wrap="none")
        scroll = ttk.Scrollbar(log_card.body, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("warn", foreground=ui.ORANGE)
        self.log.tag_configure("error", foreground=ui.RED)
        self.device_info: dict[str, str] = {}

    def _points_per_clear(self, parent: tk.Misc) -> tk.Frame:
        box = tk.Frame(parent, bg=ui.CARD)
        tk.Label(box, text="per clear", bg=ui.CARD, fg=ui.MUTED, font=(ui.FONT, 9)).pack(side="left")
        self.per_clear = tk.StringVar(value=str(config.POINTS_PER_CLEAR))
        entry = ttk.Entry(box, textvariable=self.per_clear, width=6, justify="right")
        entry.pack(side="left", padx=(4, 0))
        entry.bind("<Return>", self._save_points)
        entry.bind("<FocusOut>", self._save_points)
        ui.info(box, "The points one battle gives you. Type it here and press Enter: the points per hour and "
                     "the forecast use it.").pack(side="left")
        return box

    def _save_points(self, _=None) -> None:
        try:
            value = max(0, int(float(self.per_clear.get())))
        except ValueError:
            value = config.POINTS_PER_CLEAR
        self.per_clear.set(str(value))
        if value != config.POINTS_PER_CLEAR:
            data = settings.load()
            data["points_per_clear"] = value
            settings.save(data)
            config.reload()
            self.app._refresh_stats_now()

    def shown(self) -> None:
        self.per_clear.set(str(config.POINTS_PER_CLEAR))
        self.port.set(str(config.BRIDGE_PORT))
        self.token.set(config.BRIDGE_TOKEN)
        for child in self.rooms.winfo_children():
            child.destroy()
        self.boxes.clear()
        self.room_rows = {serial: ui.row(self.rooms, config.DEVICE_NAMES[serial], bold=True,
                                         right=lambda frame, s=serial: self._config_box(frame, s))
                          for serial in (config.DEVICE_A, config.DEVICE_B) if serial}
        if not self.room_rows:
            tk.Label(self.rooms, text="No emulators picked yet: press ⧉ on the right.", bg=ui.CARD,
                     fg=ui.MUTED, font=(ui.FONT, 9)).pack(anchor="w")
        self.tick()

    def _config_box(self, parent: tk.Misc, serial: str) -> ttk.Combobox:
        box = ttk.Combobox(parent, state="readonly", width=26, postcommand=lambda: self._load_names(serial))
        box.set(config.DUAL_CONFIGS.get(serial) or RUNNING)
        box.configure(values=[RUNNING] + self.names.get(serial, []))
        box.bind("<<ComboboxSelected>>", lambda _: self._choose(serial, box.get()))
        ui.Tooltip(box, "The clicker config this room plays when you press Start. 'Config already running' keeps "
                        "the one open in the clicker.")
        self.boxes[serial] = box
        return box

    def _load_names(self, serial: str) -> None:
        if serial in self.names:
            return
        self.names[serial] = []
        self.boxes[serial].configure(values=[RUNNING, "loading..."])

        def done(result) -> None:
            names = result if isinstance(result, list) else []
            self.names[serial] = names
            if not names:
                self.names.pop(serial)
            if serial in self.boxes:
                self.boxes[serial].configure(values=[RUNNING] + names)

        self.app.run_job(lambda: emulators.scenario_names(self.app.adb, serial, settings.DATA_DIR), done)

    def _choose(self, serial: str, name: str) -> None:
        if name == "loading...":
            self.boxes[serial].set(config.DUAL_CONFIGS.get(serial) or RUNNING)
            return
        data = settings.load()
        data["dual_configs"] = {**data["dual_configs"], serial: "" if name == RUNNING else name}
        settings.save(data)
        config.reload()

    def tick(self) -> None:
        coordinator = self.app.coordinator
        running = coordinator is not None
        busy = self.app.starting
        self.start.configure(text="■  Stop" if running else "▶  Start", bg=ui.RED if running else ui.GREEN_DARK,
                             state="disabled" if busy else "normal")
        now = time.time()
        started = self.app.started_at
        self.uptime.set(f"{stats._duration(now - started)} since Start · downtime {self.downtime}" if running and
                        started else f"stopped · downtime {self.downtime}")
        for serial, var in self.room_rows.items():
            parts = []
            if running:
                role = ("restarting" if serial in coordinator.restarting else
                        "playing" if serial == coordinator.active else "waiting" if serial == coordinator.idle
                        else "")
                if serial in coordinator.restart_pending and serial not in coordinator.restarting:
                    role = f"{role}, restart next" if role else "restart next"
                if role:
                    parts.append(role)
                event = coordinator.last_event.get(serial)
                parts.append(f"last: {event[0]} {stats._duration(now - event[1])} ago" if event else
                             "no message yet")
            if serial in self.device_info:
                parts.append(self.device_info[serial])
            var.set(" · ".join(parts) or "stopped")

    def add_log(self, level: int, line: str) -> None:
        self.log.configure(state="normal")
        tag = "error" if level >= logging.ERROR else "warn" if level >= logging.WARNING else ""
        self.log.insert("end", line + "\n", tag)
        excess = int(self.log.index("end-1c").split(".")[0]) - LOG_LINES
        if excess > 0:
            self.log.delete("1.0", f"{excess}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def show_stats(self, s: dict) -> None:
        if "error" in s:
            self.run.set(f"stats error: {s['error']}")
            return
        duration = stats._duration
        hours = int(config.STATS_UPTIME_WINDOW_HOURS)
        last = f"last clear {duration((datetime.now() - s['last_clear']).total_seconds())} ago" \
            if s["last_clear"] else "no clears yet"
        self.run.set(f"{s['session_clears']} clears · ~{s['session_clears'] * config.POINTS_PER_CLEAR} points · "
                     f"{last}")
        self.clear.set(f"avg {s['avg_clear']:.1f}s · fastest {s['fastest_clear']:.1f}s · slowest "
                       f"{s['slowest_clear']:.1f}s" if s["sample_size"] else "no data yet")
        self.downtime = duration(s["downtime_seconds"])
        self.points.set(f"~{s['points_per_hour']:.0f}/hour · ~{s['points_per_day']:.0f}/24h"
                        if s["points_per_hour"] is not None else "no data yet")
        self.target.set(f"~{s['points_to_target']:.0f} by {s['target']:%Y-%m-%d %H:%M}"
                        if s["points_to_target"] is not None else "-")
        for serial in self.room_rows:
            restarts = s["restarts"].get(serial, 0)
            up = s["device_uptime"].get(serial)
            on = f"on for {duration(up)}" if up is not None else "not reachable"
            self.device_info[serial] = f"{on} · {restarts} restart{'' if restarts == 1 else 's'} in {hours}h"
        self.tick()


class App:
    def __init__(self, root: tk.Tk, adb_path: str) -> None:
        self.root = root
        self.adb = adb_path
        self.link = handoff.link_clicker
        self.coordinator = None
        self.started_at = 0.0
        self.starting = False
        self.tray = None
        self.tutorial = False
        self.step = 0
        self.jobs: queue.Queue = queue.Queue()
        self.events: queue.Queue = queue.Queue()
        logging.getLogger("coordinator").addHandler(QueueHandler(self.events))

        root.title("ADB-YKWP-Dual")
        root.geometry("800x700")
        root.minsize(640, 620)
        root.configure(bg=ui.BACKGROUND)
        ui.style(root)

        if ICON.exists():
            self.icons = [tk.PhotoImage(file=str(IMAGES / name)) for name in
                          ("panel.png", "panel-48.png", "panel-32.png")]
            root.iconphoto(True, *self.icons)
        root.protocol("WM_DELETE_WINDOW", self.close)

        band = tk.Frame(root, bg=ui.GREEN)
        band.pack(fill="x")
        text = tk.Frame(band, bg=ui.GREEN)
        text.pack(side="left", fill="x", expand=True, padx=16, pady=10)
        self.step_label = tk.Label(text, bg=ui.GREEN, fg=ui.GREEN_LIGHT, font=(ui.FONT, 9, "bold"), anchor="w")
        self.title = tk.Label(text, bg=ui.GREEN, fg="white", font=(ui.FONT, 15, "bold"), anchor="w")
        self.title.pack(fill="x")
        self.help = tk.Label(text, bg=ui.GREEN, fg="white", font=(ui.FONT, 10), anchor="w", justify="left")
        self.help.pack(fill="x", pady=(2, 0))
        text.bind("<Configure>", lambda e: self.help.configure(wraplength=max(200, e.width - 8)))
        self.band = (band, text, self.step_label, self.title, self.help)
        self.band_color = ui.GREEN
        self.status = tk.Label(band, text="", bg=ui.GREY, fg="white", font=(ui.FONT, 9, "bold"), padx=10, pady=3)
        self.back_to_panel = tk.Button(band, text="←  Panel", bg=ui.GREEN_DARK, fg="white", relief="flat",
                                       activebackground=ui.GREEN_DARK, activeforeground="white", cursor="hand2",
                                       font=(ui.FONT, 9, "bold"), padx=10, command=lambda: self.show(self.panel))

        self.footer = tk.Frame(root, bg=ui.CARD, highlightbackground=ui.BORDER, highlightthickness=1)
        ttk.Button(self.footer, text="Skip tutorial", command=self.finish_tutorial).pack(side="left", padx=12,
                                                                                        pady=8)
        self.next = ttk.Button(self.footer, text="Next  →", style="Big.TButton", command=self.next_step)
        self.next.pack(side="right", padx=12, pady=8)
        ttk.Button(self.footer, text="←  Back", command=self.back_step).pack(side="right", pady=8)

        self.body = tk.Frame(root, bg=ui.BACKGROUND)
        self.body.pack(fill="both", expand=True, padx=16, pady=12)
        self.welcome = WelcomePage(self)
        self.emulators_page = devices.EmulatorsPage(self)
        self.config_page = prepare.ConfigPage(self)
        self.settings_page = options.SettingsPage(self)
        self.panel = PanelPage(self)
        self.steps = [self.emulators_page, self.config_page, self.settings_page]
        self.page: ui.Page | None = None

        if sys.platform == "win32" and ICON.exists():
            import tray
            self.tray = tray.Tray(str(ICON), "ADB-YKWP-Dual", self.show_window, self._tray_menu,
                                  lambda callback: self.jobs.put((lambda _: callback(), None)))
        self._poll()
        self._tick()
        self._refresh_stats()
        data = settings.load()
        if not data["tutorial_done"]:
            self.show(self.welcome)
        else:
            self.show(self.panel)
            if data["start_automatically"] and data["device_a"] and data["device_b"]:
                self.start()

    def show(self, page: ui.Page) -> None:
        if self.page:
            self.page.pack_forget()
        self.page = page
        self.title.configure(text=page.title)
        self.help.configure(text=page.help)
        if self.tutorial:
            self.step_label.configure(text=f"TUTORIAL · STEP {self.step + 1} OF {len(self.steps)}")
            self.step_label.pack(fill="x", before=self.title)
            self.footer.pack(side="bottom", fill="x", before=self.body)
            self.next.configure(text="Finish" if self.step == len(self.steps) - 1 else "Next  →")
        else:
            self.step_label.pack_forget()
            self.footer.pack_forget()
        self.status.pack_forget()
        self.back_to_panel.pack_forget()
        if page is self.panel:
            self.status.pack(side="right", padx=16)
        elif page is not self.welcome and not self.tutorial:
            self.back_to_panel.pack(side="right", padx=16)
        page.pack(fill="both", expand=True)
        page.shown()

    def start_tutorial(self) -> None:
        self.tutorial = True
        self.step = 0
        self.show(self.steps[0])

    def next_step(self) -> None:
        if not self.page.commit():
            return
        if self.step == len(self.steps) - 1:
            self.finish_tutorial()
            return
        self.step += 1
        self.show(self.steps[self.step])

    def back_step(self) -> None:
        if self.step == 0:
            self.tutorial = False
            self.show(self.welcome)
            return
        self.step -= 1
        self.show(self.steps[self.step])

    def finish_tutorial(self) -> None:
        self.tutorial = False
        data = settings.load()
        data["tutorial_done"] = True
        settings.save(data)
        config.reload()
        self.show(self.panel)

    def run_job(self, job, done) -> None:
        """Runs `job` off the interface thread, then `done(result or exception)` back on it."""
        def work() -> None:
            try:
                result = job()
            except (Exception, SystemExit) as exc:
                result = exc
            self.jobs.put((done, result))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self) -> None:
        while not self.jobs.empty():
            done, result = self.jobs.get_nowait()
            done(result)
        while not self.events.empty():
            level, line = self.events.get_nowait()
            if level is None:
                self.panel.show_stats(line)
            else:
                for serial, name in config.DEVICE_NAMES.items():
                    line = line.replace(serial, name)
                self.panel.add_log(level, line)
        self.root.after(100, self._poll)

    def _tick(self) -> None:
        coordinator = self.coordinator
        text = stats._status(coordinator) if coordinator else "Starting..." if self.starting else "Stopped"
        restarting = bool(coordinator and coordinator.restarting)
        if restarting:
            now = time.time()
            parts = []
            for serial in sorted(coordinator.restarting):
                phase, deadline = coordinator.restart_wait.get(serial, ("starting", now))
                left = f" {stats._duration(max(0, deadline - now))}" if phase == "adb check" else ""
                parts.append(f"{serial} · {phase}{left}")
            text = "Restarting " + ", ".join(parts)
        for serial, name in config.DEVICE_NAMES.items():
            text = text.replace(serial, name)
        self.status.configure(text=text, bg=STATUS_COLORS.get(text.split(" ", 1)[0], ui.GREY))
        color = ui.BLUE if restarting else ui.GREEN
        if color != self.band_color:
            self.band_color = color
            for widget in self.band:
                widget.configure(bg=color)
        if self.tray:
            self.tray.set_tip(f"ADB-YKWP-Dual · {text}")
        self.panel.tick()
        self.root.after(1000, self._tick)

    def _refresh_stats_now(self) -> None:
        self.run_job(self._stats_job(), self.panel.show_stats)

    def _stats_job(self):
        serials = [s for s in (config.DEVICE_A, config.DEVICE_B) if s]

        def job() -> dict:
            try:
                result = stats.compute_stats()
                result["device_uptime"] = {serial: adb.uptime(serial) for serial in serials}
            except Exception as exc:
                result = {"error": str(exc)}
            return result

        return job

    def _refresh_stats(self) -> None:
        self._refresh_stats_now()
        self.root.after(int(config.STATS_INTERVAL_SECONDS * 1000), self._refresh_stats)

    def toggle(self) -> None:
        if self.coordinator:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        config.reload()
        if not (config.DEVICE_A and config.DEVICE_B):
            self.show(self.emulators_page)
            self.emulators_page.status.set("Tick the two instances to use, then press 'Use these two instances'.")
            return
        if self.starting or self.coordinator:
            return
        self.starting = True

        def job():
            devices = (config.DEVICE_A, config.DEVICE_B)
            problems = [problem for problem in map(handoff.check_device, devices) if problem]
            if problems:
                return RuntimeError("; ".join(problems))
            for attempt in range(10):
                try:
                    coordinator = handoff.start()
                    break
                except OSError:
                    if attempt == 9:
                        raise
                    time.sleep(0.5)
            workers = [threading.Thread(target=handoff.prepare_device, args=(serial,), daemon=True)
                       for serial in (config.DEVICE_A, config.DEVICE_B)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            return coordinator

        def done(result) -> None:
            self.starting = False
            if isinstance(result, RuntimeError):
                logging.getLogger("coordinator").error("not started: %s", result)
                return
            if isinstance(result, BaseException):
                messagebox.showerror("ADB-YKWP-Dual", f"Could not start: {result}")
                return
            self.coordinator = result
            self.started_at = time.time()

        self.run_job(job, done)

    def stop(self, then=None) -> None:
        coordinator, self.coordinator = self.coordinator, None
        if not coordinator:
            return
        self.starting = True

        def done(_) -> None:
            self.starting = False
            if then:
                then()

        self.run_job(lambda: handoff.stop(coordinator), done)

    def pair_changed(self) -> None:
        config.reload()
        self.panel.device_info.clear()
        if self.coordinator:
            self.stop(then=self.start)

    def settings_saved(self) -> None:
        handoff.set_file_logging(config.WRITE_LOGS)

    def restart_now(self) -> bool:
        if not self.coordinator or self.coordinator.idle is None:
            messagebox.showinfo("Restart", "Start first and wait until one emulator is playing: the waiting one "
                                           "restarts first, then the other.")
            return False
        self.coordinator.restart_requested = True
        logging.getLogger("coordinator").info("restart of both emulators asked from the panel")
        return True

    def _tray_menu(self) -> list:
        running = self.coordinator is not None
        return [("Open panel", self.show_window, True),
                ("Stop" if running else "Start", self.toggle, not self.starting),
                ("Restart both now", self.restart_now, running),
                None,
                ("Exit", self.quit, True)]

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def close(self) -> None:
        choice = settings.load()["on_close"] if self.tray else "exit"
        if choice == "ask":
            choice = self._ask_close()
        if choice == "tray":
            self.root.withdraw()
        elif choice == "exit":
            self.quit()

    def _ask_close(self) -> str | None:
        dialog = tk.Toplevel(self.root, bg=ui.CARD, padx=20, pady=16)
        dialog.title("ADB-YKWP-Dual")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        answer: list[str | None] = [None]
        remember = tk.BooleanVar()
        tk.Label(dialog, text="Keep the program running in the tray, next to the clock,\nor close it completely?",
                 bg=ui.CARD, fg=ui.TEXT, font=(ui.FONT, 10), justify="left").pack(anchor="w")
        ttk.Checkbutton(dialog, text="Don't ask again (change it in Settings)", variable=remember).pack(
            anchor="w", pady=(10, 12))
        buttons = tk.Frame(dialog, bg=ui.CARD)
        buttons.pack(fill="x")

        def pick(choice: str | None) -> None:
            answer[0] = choice
            if choice and remember.get():
                data = settings.load()
                data["on_close"] = choice
                settings.save(data)
            dialog.destroy()

        ttk.Button(buttons, text="Minimize to tray", style="Big.TButton", command=lambda: pick("tray")).pack(
            side="left")
        ttk.Button(buttons, text="Close completely", command=lambda: pick("exit")).pack(side="left", padx=8)
        ttk.Button(buttons, text="Cancel", command=lambda: pick(None)).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 3
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()
        self.root.wait_window(dialog)
        return answer[0]

    def quit(self) -> None:
        if self.tray:
            self.tray.close()
        coordinator, self.coordinator = self.coordinator, None
        if coordinator:
            handoff.stop(coordinator)
        self.root.destroy()

    @staticmethod
    def open_path(path) -> None:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def run() -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        adb_path = settings.ensure_adb(lambda: messagebox.askyesno(
            "adb not found", "adb (Android platform-tools) was not found on this computer.\n\n"
                             "Download it from Google now?"))
    except SystemExit as exc:
        messagebox.showerror("ADB-YKWP-Dual", str(exc.code))
        root.destroy()
        return
    adb.ADB_BIN = adb_path
    settings.ensure_token()
    config.reload()
    App(root, adb_path)
    root.deiconify()
    root.mainloop()
