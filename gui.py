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


MODES = {
    "dual": ("⇄  Dual", "Two rooms take turns: one plays a stage while the other logs in, then they swap. "
                       "Needs two emulator instances."),
    "single": ("▶  Single", "Each room plays on its own with its clicker config. The program watches every room: "
                           "when one stops clearing battles it closes and opens the game, then restarts the "
                           "emulator. One room or more."),
}


def app_title() -> str:
    return "ADB-YKWP-Dual · Single" if config.MODE == "single" else "ADB-YKWP-Dual"


class ModePage(ui.Page):
    title = "Choose the mode"
    help = "Pick how the rooms play. You can change it later with the mode button on the panel."

    def __init__(self, app) -> None:
        super().__init__(app)
        box = tk.Frame(self, bg=ui.BACKGROUND)
        box.place(relx=0.5, rely=0.42, anchor="center")
        for mode, (name, text) in MODES.items():
            card = tk.Frame(box, bg=ui.CARD, highlightbackground=ui.BORDER, highlightthickness=1, padx=18, pady=14)
            card.pack(fill="x", pady=6)
            tk.Label(card, text=name, bg=ui.CARD, fg=ui.GREEN_DARK, font=(ui.FONT, 14, "bold")).pack(anchor="w")
            tk.Label(card, text=text, bg=ui.CARD, fg=ui.MUTED, font=(ui.FONT, 10), justify="left",
                     wraplength=440).pack(anchor="w", pady=(4, 10))
            ttk.Button(card, text=f"Use {name.split()[-1].lower()} mode", style="Big.TButton",
                       command=lambda m=mode: app.choose_mode(m)).pack(anchor="e")
        self.ask = tk.BooleanVar()
        ttk.Checkbutton(box, text="Ask every time the program opens", variable=self.ask,
                        style="Page.TCheckbutton").pack(anchor="w", pady=(8, 0))

    def shown(self) -> None:
        self.ask.set(settings.load()["ask_mode"])


class WelcomePage(ui.Page):
    title = "Welcome"

    @property
    def help(self) -> str:
        if config.MODE == "single":
            return "Single mode: each room plays on its own, and the program makes sure none of them gets stuck."
        return "This program makes two emulators take turns playing, so one is always logging in while the other plays."

    def __init__(self, app) -> None:
        super().__init__(app)
        box = tk.Frame(self, bg=ui.BACKGROUND)
        box.place(relx=0.5, rely=0.42, anchor="center")
        tk.Label(box, text="First time here?", bg=ui.BACKGROUND, fg=ui.TEXT, font=(ui.FONT, 16, "bold")).pack()
        self.text = tk.Label(box, bg=ui.BACKGROUND, fg=ui.MUTED, font=(ui.FONT, 10), justify="center")
        self.text.pack(pady=(8, 20))
        buttons = tk.Frame(box, bg=ui.BACKGROUND)
        buttons.pack()
        ttk.Button(buttons, text="Start tutorial", style="Big.TButton", command=app.start_tutorial).pack(
            side="left", padx=6)
        ttk.Button(buttons, text="Skip tutorial", command=app.finish_tutorial).pack(side="left", padx=6)

    def shown(self) -> None:
        what = "the instances to watch" if config.MODE == "single" else "the emulators"
        self.text.configure(text=f"The tutorial walks you through the setup in three short steps:\n"
                                 f"{what}, the clicker config and the extra settings.")


class PanelPage(ui.Page):
    @property
    def title(self) -> str:
        return app_title()

    @property
    def help(self) -> str:
        if config.MODE == "single":
            return "Check the config each room plays in the menu next to it, then press Start. ⟳ restarts one room."
        return "Check the config each room plays in the menu next to it, then press Start."

    def __init__(self, app) -> None:
        super().__init__(app)
        toolbar = tk.Frame(self, bg=ui.BACKGROUND)
        toolbar.pack(fill="x")
        self.start = tk.Button(toolbar, text="▶  Start", width=10, font=(ui.FONT, 11, "bold"), fg="white",
                               bg=ui.GREEN_DARK, activebackground=ui.GREEN, activeforeground="white", relief="flat",
                               cursor="hand2", command=app.toggle)
        self.start.pack(side="left", ipady=4)
        for symbol, tip, command in (("?", "Tutorial", app.start_tutorial),
                                     ("⇄", "Change mode (dual or single)", app.change_mode),
                                     ("⚙", "Settings", lambda: app.show(app.settings_page)),
                                     ("✎", "Clicker config", lambda: app.show(app.config_page)),
                                     ("⧉", "Emulators", lambda: app.show(app.emulators_page))):
            ui.icon_button(toolbar, symbol, tip, command).pack(side="right", padx=(6, 0))

        self.boxes: dict[str, ttk.Combobox] = {}
        self.names: dict[str, list[str]] = {}
        self.tiles: dict[str, RoomTile] = {}
        self.rooms = tk.Frame(self, bg=ui.BACKGROUND)
        self.rooms.pack(fill="x", pady=(10, 0))

        totals = tk.Frame(self, bg=ui.CARD, highlightbackground=ui.BORDER, highlightthickness=1, padx=14, pady=10)
        self.totals = totals
        head = tk.Frame(totals, bg=ui.CARD)
        head.pack(fill="x")
        self.totals_title = tk.Label(head, bg=ui.CARD, fg=ui.GREEN_DARK, font=(ui.FONT, 10, "bold"))
        self.totals_title.pack(side="left")
        self._points_per_clear(head).pack(side="right")
        cells = tk.Frame(totals, bg=ui.CARD)
        cells.pack(fill="x", pady=(4, 0))
        self.total = {key: figure(cells, caption, index) for index, (key, caption) in enumerate(
            (("clears", "clears"), ("points", "points"), ("hour", "points / hour"), ("avg", "avg clear"),
             ("uptime", "since Start")))}
        self.total_info = tk.Label(totals, bg=ui.CARD, fg=ui.MUTED, font=(ui.FONT, 9), anchor="w")
        self.total_info.pack(fill="x", pady=(6, 0))
        self.downtime = "0s"
        self.extra = ""

        log_card = ui.Card(self, "Log")
        log_card.pack(fill="both", expand=True, pady=(10, 0))
        self.log_card = log_card
        self.log = tk.Text(log_card.body, height=4, bg="#FCFCFC", fg=ui.TEXT, relief="flat", font=("Consolas", 9),
                           state="disabled", wrap="none")
        scroll = ttk.Scrollbar(log_card.body, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("warn", foreground=ui.ORANGE)
        self.log.tag_configure("error", foreground=ui.RED)
        self.device_info: dict[str, str] = {}
        self.room_stats: dict[str, dict] = {}

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
        single = config.MODE == "single"
        self.totals_title.configure(text="Stats")
        if single:
            self.totals.pack_forget()
        else:
            self.totals.pack(fill="x", pady=(10, 0), before=self.log_card)
        for child in self.rooms.winfo_children():
            child.destroy()
        self.boxes.clear()
        rooms = [serial for serial in config.DEVICES if serial]
        columns = min(3, max(1, len(rooms)))
        for column in range(3):
            used = column < columns
            self.rooms.columnconfigure(column, weight=1 if used else 0, uniform="rooms" if used else "")
        self.tiles = {serial: RoomTile(self.rooms, self, serial, single) for serial in rooms}
        for index, tile in enumerate(self.tiles.values()):
            tile.grid(row=index // columns, column=index % columns, sticky="nsew",
                      padx=(0 if index % columns == 0 else 5, 0 if index % columns == columns - 1 else 5),
                      pady=(0 if index < columns else 10, 0))
        if not rooms:
            tk.Label(self.rooms, text="No emulators picked yet: press ⧉ above.", bg=ui.BACKGROUND,
                     fg=ui.MUTED, font=(ui.FONT, 10)).grid(row=0, column=0, sticky="w")
        self.tick()

    def _config_box(self, parent: tk.Misc, serial: str) -> ttk.Combobox:
        box = ttk.Combobox(parent, state="readonly", width=18, postcommand=lambda: self._load_names(serial))
        box.set(config.CONFIGS.get(serial) or RUNNING)
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
            self.boxes[serial].set(config.CONFIGS.get(serial) or RUNNING)
            return
        data = settings.load()
        key = "single_configs" if config.MODE == "single" else "dual_configs"
        data[key] = {**data[key], serial: "" if name == RUNNING else name}
        settings.save(data)
        config.reload()

    def tick(self) -> None:
        coordinator = self.app.coordinator
        running = coordinator is not None
        single = config.MODE == "single"
        self.start.configure(text="■  Stop" if running else "▶  Start", bg=ui.RED if running else ui.GREEN_DARK,
                             state="disabled" if self.app.starting else "normal")
        now = time.time()
        started = self.app.started_at
        self.total["uptime"].configure(text=stats._duration(now - started) if running and started else "-")
        self.total_info.configure(text=f"downtime {self.downtime}{self.extra}")
        for serial, tile in self.tiles.items():
            if not running:
                tile.state("STOPPED", ui.GREY, "")
                continue
            if single and serial not in coordinator.rooms:
                tile.state("NOT STARTED", ui.GREY, "not ready at Start: see the log")
                continue
            quiet = now - coordinator.last_clear[serial] if single else 0
            if serial in coordinator.restarting:
                phase = coordinator.restart_wait.get(serial, ("starting", now))
                tile.state("RESTARTING", ui.BLUE, phase[0])
            elif single and serial in coordinator.stuck_since:
                tile.state("STUCK", ui.RED, f"no clear for {stats._duration(quiet)}")
            elif single:
                tile.state("PLAYING", ui.GREEN_DARK, f"last clear {stats._duration(quiet)} ago"
                           if serial in coordinator.cleared else "no clear yet")
            else:
                event = coordinator.last_event.get(serial)
                last = f"last: {event[0]} {stats._duration(now - event[1])} ago" if event else "no message yet"
                stuck = serial == coordinator.active and coordinator.stuck_since is not None
                tile.state("STUCK" if stuck else "PLAYING" if serial == coordinator.active else "WAITING",
                           ui.RED if stuck else ui.GREEN_DARK if serial == coordinator.active else ui.GREY, last)
            if serial in coordinator.restart_pending and serial not in coordinator.restarting:
                tile.detail.configure(text=tile.detail.cget("text") + " · restart next")

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
            self.extra = f" · stats error: {s['error']}"
            return
        duration = stats._duration
        hours = int(config.STATS_UPTIME_WINDOW_HOURS)
        clears = s["session_clears"]
        self.total["clears"].configure(text=str(clears))
        self.total["points"].configure(text=short(clears * config.POINTS_PER_CLEAR))
        self.total["hour"].configure(text=short(s["points_per_hour"]) if s["points_per_hour"] else "-")
        self.total["avg"].configure(text=f"{s['avg_clear']:.2f}s" if s["sample_size"] else "-")
        self.downtime = duration(s["downtime_seconds"])
        extra = []
        if s["sample_size"]:
            extra.append(f"fastest {s['fastest_clear']:.2f}s · slowest {s['slowest_clear']:.2f}s")
        if s["points_per_day"]:
            extra.append(f"~{short(s['points_per_day'])} points / 24h")
        if s["points_to_target"] is not None:
            extra.append(f"~{short(s['points_to_target'])} by {s['target']:%Y-%m-%d %H:%M}")
        self.extra = "".join(f" · {part}" for part in extra)
        for serial, tile in self.tiles.items():
            restarts = s["restarts"].get(serial, 0)
            up = s["device_uptime"].get(serial)
            on = f"on for {duration(up)}" if up is not None else "not reachable"
            info = f"{on} · {restarts} restart{'' if restarts == 1 else 's'} in {hours}h"
            room = s["rooms"].get(serial)
            if config.MODE == "single":
                room = room or {"clears": 0, "avg_clear": None, "downtime_seconds": 0}
                points = config.ROOM_POINTS.get(serial, config.POINTS_PER_CLEAR)
                tile.figures["clears"].configure(text=str(room["clears"]))
                tile.figures["points"].configure(text=short(room["clears"] * points))
                tile.figures["avg"].configure(text=f"{room['avg_clear']:.2f}s" if room["avg_clear"] else "-")
                tile.figures["hour"].configure(text=short(points / room["avg_clear"] * 3600)
                                               if room["avg_clear"] else "-")
                info += f" · downtime {duration(room['downtime_seconds'])}"
            tile.info.configure(text=info)
        self.tick()


def short(number: float) -> str:
    if number >= 1000000:
        return f"{number / 1000000:.3f}M"
    if number >= 1000:
        return f"{number / 1000:.2f}k"
    return f"{number:.0f}"


def figure(parent: tk.Misc, caption: str, column: int) -> tk.Label:
    parent.columnconfigure(column, weight=1, uniform="figures")
    cell = tk.Frame(parent, bg=ui.CARD)
    cell.grid(row=0, column=column, sticky="w", pady=2)
    value = tk.Label(cell, text="-", bg=ui.CARD, fg=ui.TEXT, font=(ui.FONT, 16, "bold"))
    value.pack(anchor="w")
    tk.Label(cell, text=caption, bg=ui.CARD, fg=ui.MUTED, font=(ui.FONT, 8)).pack(anchor="w")
    return value


def unlink_button(parent: tk.Misc, command) -> tk.Frame:
    """A blue box with a broken chain and the word Disconnect."""
    box = tk.Frame(parent, bg=ui.BLUE, cursor="hand2", padx=4)
    canvas = tk.Canvas(box, width=20, height=18, bg=ui.BLUE, highlightthickness=0)
    canvas.create_line(3, 13, 6, 10, 9, 10, 10, 11, 10, 13, 7, 16, 5, 16, 3, 14, 3, 13, fill="white", width=2)
    canvas.create_line(18, 5, 15, 8, 12, 8, 11, 7, 11, 5, 14, 2, 16, 2, 18, 4, 18, 5, fill="white", width=2)
    canvas.create_line(8, 3, 9, 6, fill="white", width=2)
    canvas.create_line(13, 15, 12, 12, fill="white", width=2)
    canvas.pack(side="left")
    label = tk.Label(box, text="Disconnect", bg=ui.BLUE, fg="white", font=(ui.FONT, 8, "bold"))
    label.pack(side="left", padx=(2, 2))
    for widget in (box, canvas, label):
        widget.bind("<Button-1>", lambda _: command())
    ui.Tooltip(box, "Disconnect this emulator: it is taken out of the rooms. Add it back from ⧉.")
    return box


class RoomTile(tk.Frame):
    """One room: its state, its numbers in single mode, the config it plays and its restart button."""

    def __init__(self, parent: tk.Misc, panel: PanelPage, serial: str, single: bool) -> None:
        super().__init__(parent, bg=ui.CARD, highlightbackground=ui.BORDER, highlightthickness=1, padx=12, pady=10)
        tk.Label(self, text=config.DEVICE_NAMES.get(serial, serial), bg=ui.CARD, fg=ui.TEXT,
                 font=(ui.FONT, 12, "bold"), anchor="w").pack(fill="x")
        state = tk.Frame(self, bg=ui.CARD)
        state.pack(fill="x", pady=(4, 0))
        unlink_button(state, lambda: panel.app.unlink(serial)).pack(side="left", padx=(0, 6))
        self.pill = tk.Label(state, text="STOPPED", bg=ui.GREY, fg="white", font=(ui.FONT, 8, "bold"), padx=8,
                             pady=1)
        self.pill.pack(side="left")
        self.detail = tk.Label(self, bg=ui.CARD, fg=ui.TEXT, font=(ui.FONT, 9), anchor="w")
        self.detail.pack(fill="x", pady=(4, 0))
        self.figures: dict[str, tk.Label] = {}
        if single:
            grid = tk.Frame(self, bg=ui.CARD)
            grid.pack(fill="x", pady=(6, 0))
            for index, (key, caption) in enumerate((("clears", "clears"), ("points", "points"),
                                                     ("avg", "avg clear"), ("hour", "points / hour"))):
                grid.columnconfigure(index % 2, weight=1, uniform="figures")
                cell = tk.Frame(grid, bg=ui.CARD)
                cell.grid(row=index // 2, column=index % 2, sticky="w", pady=2)
                value = tk.Label(cell, text="-", bg=ui.CARD, fg=ui.TEXT, font=(ui.FONT, 15, "bold"))
                value.pack(anchor="w")
                tk.Label(cell, text=caption, bg=ui.CARD, fg=ui.MUTED, font=(ui.FONT, 8)).pack(anchor="w")
                self.figures[key] = value
            points = tk.Frame(self, bg=ui.CARD)
            points.pack(fill="x", pady=(4, 0))
            tk.Label(points, text="points per clear", bg=ui.CARD, fg=ui.MUTED, font=(ui.FONT, 9)).pack(side="left")
            self.per_clear = tk.StringVar(value=str(config.ROOM_POINTS.get(serial, config.POINTS_PER_CLEAR)))
            entry = ttk.Entry(points, textvariable=self.per_clear, width=6, justify="right")
            entry.pack(side="left", padx=(6, 0))
            entry.bind("<Return>", lambda _: self._save_points(serial, panel))
            entry.bind("<FocusOut>", lambda _: self._save_points(serial, panel))
            ui.info(points, "The points one battle gives in this room. Type it and press Enter: the points of "
                            "this room use it.").pack(side="left")
        tools = tk.Frame(self, bg=ui.CARD)
        tools.pack(fill="x", pady=(8, 0))
        if single:
            button = ttk.Button(tools, text="⟳", width=3, command=lambda: panel.app.restart_room(serial))
            button.pack(side="right", padx=(4, 0))
            ui.Tooltip(button, "Restart this room now (LDPlayer only)")
        panel._config_box(tools, serial).pack(side="left", fill="x", expand=True)
        self.info = tk.Label(self, bg=ui.CARD, fg=ui.MUTED, font=(ui.FONT, 8), anchor="w", justify="left")
        self.info.pack(fill="x", pady=(4, 0))
        self.info.bind("<Configure>", lambda e: self.info.configure(wraplength=max(80, e.width - 4)))

    def _save_points(self, serial: str, panel: PanelPage) -> None:
        current = config.ROOM_POINTS.get(serial, config.POINTS_PER_CLEAR)
        try:
            value = max(0, int(float(self.per_clear.get())))
        except ValueError:
            value = current
        self.per_clear.set(str(value))
        if value != current:
            data = settings.load()
            data["room_points"] = {**data["room_points"], serial: value}
            settings.save(data)
            config.reload()
            panel.app._refresh_stats_now()

    def state(self, name: str, color: str, detail: str) -> None:
        self.pill.configure(text=name, bg=color)
        self.detail.configure(text=detail)


class App:
    def __init__(self, root: tk.Tk, adb_path: str) -> None:
        self.root = root
        self.adb = adb_path
        self.link = handoff.link_clicker
        self.coordinator = None
        self.started_at = 0.0
        self.starting = False
        self.tray = None
        self.opened = False
        self.tutorial = False
        self.step = 0
        self.jobs: queue.Queue = queue.Queue()
        self.events: queue.Queue = queue.Queue()
        logging.getLogger("coordinator").addHandler(QueueHandler(self.events))

        root.title(app_title())
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
        self.mode_page = ModePage(self)
        self.emulators_page = devices.EmulatorsPage(self)
        self.dual_config_page = prepare.ConfigPage(self)
        self.solo_config_page = prepare.SoloConfigPage(self)
        self.settings_page = options.SettingsPage(self)
        self.panel = PanelPage(self)
        self.page: ui.Page | None = None

        if sys.platform == "win32" and ICON.exists():
            import tray
            self.tray = tray.Tray(str(ICON), "ADB-YKWP-Dual", self.show_window, self._tray_menu,
                                  lambda callback: self.jobs.put((lambda _: callback(), None)))
        self._poll()
        self._tick()
        self._refresh_stats()
        if settings.load()["ask_mode"]:
            self.show(self.mode_page)
        else:
            self.open_mode(autostart=True)

    def _tutorial_key(self) -> str:
        return "single_tutorial_done" if config.MODE == "single" else "tutorial_done"

    def choose_mode(self, mode: str) -> None:
        data = settings.load()
        changed = data["mode"] != mode
        data["mode"] = mode
        data["ask_mode"] = self.mode_page.ask.get()
        settings.save(data)
        if changed:
            self.panel.names.clear()
            self.panel.device_info.clear()
            self.panel.room_stats.clear()
        self.open_mode(autostart=not self.opened)

    def open_mode(self, autostart: bool) -> None:
        config.reload()
        self.opened = True
        self.root.title(app_title())
        if not settings.load()[self._tutorial_key()]:
            self.show(self.welcome)
            return
        self.show(self.panel)
        if autostart and config.MODE == "dual" and settings.load()["start_automatically"] and self._rooms_ready():
            self.start()

    def _rooms_ready(self) -> bool:
        return bool(config.DEVICES) if config.MODE == "single" else bool(config.DEVICE_A and config.DEVICE_B)

    def change_mode(self) -> None:
        if self.coordinator:
            if not messagebox.askyesno(app_title(), "Stop playing and change the mode?"):
                return
            self.stop(then=lambda: self.show(self.mode_page))
            return
        self.show(self.mode_page)

    @property
    def config_page(self) -> ui.Page:
        return self.solo_config_page if config.MODE == "single" else self.dual_config_page

    @property
    def steps(self) -> list:
        return [self.emulators_page, self.config_page, self.settings_page]

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
        elif page not in (self.welcome, self.mode_page) and not self.tutorial:
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
        data[self._tutorial_key()] = True
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
        serials = [s for s in config.DEVICES if s]

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
        if not self._rooms_ready():
            self.show(self.emulators_page)
            self.emulators_page.status.set("Tick the instances to use, then press 'Use these instances'."
                                           if config.MODE == "single" else
                                           "Tick the two instances to use, then press 'Use these two instances'.")
            return
        if self.starting or self.coordinator:
            return
        self.starting = True

        def job():
            devices = config.DEVICES
            problems = {serial: problem for serial in devices if (problem := handoff.check_device(serial))}
            if config.MODE == "single":
                for problem in problems.values():
                    logging.getLogger("coordinator").error("not started: %s", problem)
                devices = tuple(serial for serial in devices if serial not in problems)
                if not devices:
                    return RuntimeError("no room is ready")
            elif problems:
                return RuntimeError("; ".join(problems.values()))
            for attempt in range(10):
                try:
                    coordinator = handoff.start(rooms=devices)
                    break
                except OSError:
                    if attempt == 9:
                        raise
                    time.sleep(0.5)
            workers = [threading.Thread(target=handoff.prepare_device, args=(serial,), daemon=True)
                       for serial in devices]
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

    def unlink(self, serial: str) -> None:
        name = config.DEVICE_NAMES.get(serial, serial)
        if not messagebox.askyesno(app_title(), f"Disconnect {name}?\n\nIt is taken out of the rooms and its clicker "
                                                f"is no longer linked to this computer. Add it back from ⧉."):
            return
        data = settings.load()
        if config.MODE == "single":
            data["single_devices"] = [s for s in data["single_devices"] if s != serial]
        else:
            for key in ("device_a", "device_b"):
                if data[key] == serial:
                    data[key] = data[f"{key}_name"] = ""
        settings.save(data)
        port = config.BRIDGE_PORT
        self.run_job(lambda: adb.unlink(serial, port), lambda _: None)
        logging.getLogger("coordinator").info("%s disconnected from the panel", serial)
        config.reload()
        self.panel.device_info.clear()
        self.panel.room_stats.clear()
        self.panel.shown()
        if self.coordinator:
            self.stop(then=self.start if self._rooms_ready() else None)

    def pair_changed(self) -> None:
        config.reload()
        self.panel.device_info.clear()
        self.panel.room_stats.clear()
        if self.coordinator:
            self.stop(then=self.start)

    def settings_saved(self) -> None:
        handoff.set_file_logging(config.WRITE_LOGS)

    def restart_room(self, serial: str) -> None:
        if not self.coordinator:
            messagebox.showinfo("Restart", "Press Start first.")
        elif not self.coordinator.restart_room(serial):
            messagebox.showinfo("Restart", "This room is already restarting, or it is not an open LDPlayer instance.")
        else:
            logging.getLogger("coordinator").info("restart of %s asked from the panel", serial)

    def restart_now(self) -> bool:
        if config.MODE == "single" and self.coordinator:
            self.coordinator.restart_requested = True
            logging.getLogger("coordinator").info("restart of all rooms asked from the panel, one at a time")
            return True
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
                ("Restart all now" if config.MODE == "single" else "Restart both now", self.restart_now, running),
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
        handoff.delete_logs()
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
