"""Clicker page: turns one of your clicker configs into a dual-ready copy and installs it on both emulators."""
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import config
import configs
import emulators
import settings
import ui

NONE = "(none)"
FROM_PC = "A file on this PC..."


SINGLE_EVENT_TIP = ("The event that runs once at the end of every battle, like the one that presses OK on the defeat "
                    "or the stage clear screen. Each time it runs, the clicker tells this program the room is still "
                    "playing. When it stops, the program closes and opens the game, then restarts the emulator.")


class ConfigPage(ui.Page):
    title = "Clicker config"
    help = ("1  Choose where your clicker config is: in one of the emulators, or a backup file on this PC.\n"
            "2  Choose the config, then the event that presses OK after a stage clear and the one that sees "
            "the login screen.\n"
            "3  Press Send: both emulators get a ready copy called '(dual ...)'.\n"
            "4  On the panel, the menu next to each room is the config that room plays at Start: Send picks the new "
            "copy for you, and you can change it there any time.")

    def __init__(self, app) -> None:
        super().__init__(app)
        self.source: Path | None = None
        self.scenarios: list[configs.Scenario] = []
        self.folder = tempfile.TemporaryDirectory()
        self.busy = False

        body = tk.Frame(self, bg=ui.BACKGROUND)
        body.pack(fill="x")
        body.columnconfigure(2, weight=1)

        self.where = self._row(body, 0, 1, "Config is in",
                               "Pick an emulator to read the configs saved in its clicker, or pick a backup "
                               ".zip exported from the clicker.")
        self.where.bind("<<ComboboxSelected>>", lambda _: self._load_source())
        self.scenario = self._row(body, 1, 2, "Config", "The config you normally use to play the stages.")
        self.scenario.bind("<<ComboboxSelected>>", lambda _: self._scenario_changed())
        self.clear_event = self._row(body, 2, None, "Stage clear event",
                                     "The event that presses OK on the stage clear screen. When it runs, the "
                                     "clicker tells this program the stage is done, so the other emulator "
                                     "starts playing.")
        self.login_event = self._row(body, 3, None, "Login screen event",
                                     "The event that sees the game's login (title) screen. Its own clicks are "
                                     "removed: it only tells this program the emulator is waiting, and this "
                                     "program taps login when it is that emulator's turn.")
        for combo in (self.clear_event, self.login_event):
            combo.bind("<<ComboboxSelected>>", lambda _: self._update())

        actions = tk.Frame(self, bg=ui.BACKGROUND)
        actions.pack(fill="x", pady=(14, 0))
        ui.badge(actions, 3).pack(side="left")
        self.send = ttk.Button(actions, text="Send to both emulators", style="Big.TButton", command=self._send)
        self.send.pack(side="left", padx=6)
        ui.info(actions, "Your config is not changed. Each emulator gets its own copy with the dual commands "
                         "added, and the program links both clickers to this computer.", ui.BACKGROUND).pack(
            side="left")

        self.status = tk.StringVar()
        tk.Label(self, textvariable=self.status, bg=ui.BACKGROUND, fg=ui.MUTED, anchor="w", justify="left",
                 wraplength=640).pack(fill="x", pady=(10, 0))
        self._update()

    def _row(self, parent, row: int, number: int | None, label: str, tip: str) -> ttk.Combobox:
        if number:
            ui.badge(parent, number).grid(row=row, column=0, padx=(0, 6), pady=5)
        tk.Label(parent, text=label, bg=ui.BACKGROUND, fg=ui.TEXT, font=(ui.FONT, 10)).grid(
            row=row, column=1, sticky="w", pady=5)
        combo = ttk.Combobox(parent, state="readonly", width=40)
        combo.grid(row=row, column=2, sticky="ew", padx=8, pady=5)
        ui.info(parent, tip, ui.BACKGROUND).grid(row=row, column=3)
        return combo

    def _devices(self) -> list[tuple[str, str]]:
        config.reload()
        return [(s, config.DEVICE_NAMES[s]) for s in (config.DEVICE_A, config.DEVICE_B) if s]

    def shown(self) -> None:
        devices = self._devices()
        self.where.configure(values=[f"Clicker on {name}" for _, name in devices] + [FROM_PC])
        if len(devices) < 2:
            self.status.set("Pick the two emulators first (emulators page).")
        self._update()

    def _load_source(self) -> None:
        index = self.where.current()
        devices = self._devices()
        if index >= len(devices):
            path = filedialog.askopenfilename(parent=self, title="Clicker backup",
                                              filetypes=[("Clicker backup", "*.zip")])
            if path:
                self._load(Path(path))
            return
        serial, name = devices[index]
        local = Path(self.folder.name) / f"{_safe(name)} configs.zip"
        adb = self.app.adb
        self._work(f"Reading the configs in the clicker on {name}...",
                   lambda: emulators.export_scenarios(adb, serial, local),
                   lambda ok: self._load(local) if ok else self.status.set(
                       f"Could not read the clicker on {name}. Is it installed and opened once?"))

    def _load(self, path: Path) -> None:
        self.source = path
        self.scenarios = configs.read(path)
        self.scenario.configure(values=[s.name for s in self.scenarios])
        self.scenario.set(self.scenarios[0].name if self.scenarios else "")
        self.status.set(f"{len(self.scenarios)} config(s) found." if self.scenarios else "No config found.")
        self._scenario_changed()

    def _scenario_changed(self) -> None:
        scenario = self._chosen()
        names = [NONE] + [f"{name}  #{event_id}" for event_id, name in scenario.events] if scenario else []
        for combo in (self.clear_event, self.login_event):
            combo.configure(values=names)
            combo.set(NONE if scenario else "")
        self._update()

    def _chosen(self) -> configs.Scenario | None:
        index = self.scenario.current()
        return self.scenarios[index] if 0 <= index < len(self.scenarios) else None

    def _event(self, combo: ttk.Combobox) -> int | None:
        scenario = self._chosen()
        index = combo.current() - 1
        return scenario.events[index][0] if scenario and index >= 0 else None

    def _update(self) -> None:
        ready = (not self.busy and len(self._devices()) == 2 and self._chosen() is not None
                 and (self._event(self.clear_event) or self._event(self.login_event)))
        self.send.state(["!disabled"] if ready else ["disabled"])

    def _work(self, message: str, job, done) -> None:
        self.busy = True
        self.status.set(message)
        self._update()

        def finish(result) -> None:
            self.busy = False
            if isinstance(result, Exception):
                self.status.set(f"Error: {result}")
            else:
                done(result)
            self._update()

        self.app.run_job(job, finish)

    def _send(self) -> None:
        scenario = self._chosen()
        clear, login = self._event(self.clear_event), self._event(self.login_event)
        devices, adb, source, link = self._devices(), self.app.adb, self.source, self.app.link

        def job() -> list[str]:
            sent, names = [], {}
            for serial, name in devices:
                target = Path(self.folder.name) / f"{_safe(scenario.name)} - dual {_safe(name)}.zip"
                dual = f"{scenario.name} (dual {name})"
                configs.prepare(source, target, scenario, clear, login, serial, dual)
                if emulators.import_scenarios(adb, serial, target):
                    link(serial)
                    sent.append(name)
                    names[serial] = dual
            if names:
                data = settings.load()
                data["dual_configs"] = {**data["dual_configs"], **names}
                settings.save(data)
                config.reload()
            return sent

        self._work("Preparing and sending...", job, lambda sent: self.status.set(
            f"Done on {', '.join(sent)}. Press Start on the panel: it opens the game and plays "
            f"'{scenario.name} (dual ...)' by itself." if sent else
            "Nothing was sent: check that the clicker is installed on both emulators."))


def _safe(text: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in text).strip()


class SoloConfigPage(ui.Page):
    """Single mode: every room keeps its own config, so each one is read from and sent back to its own clicker."""
    title = "Clicker config"
    help = ("1  Press Read configs: the program reads the configs saved in the clicker of every room.\n"
            "2  For each room choose its config and the event that runs once at the end of every battle, like the "
            "one that presses OK on the defeat or the stage clear screen.\n"
            "3  Press Send: each room gets a copy of its own config called '(solo ...)', and the panel picks it "
            "for that room.")

    def __init__(self, app) -> None:
        super().__init__(app)
        self.folder = tempfile.TemporaryDirectory()
        self.busy = False
        self.rooms: list[tuple[str, str]] = []
        self.found: dict[str, tuple[Path, list[configs.Scenario]]] = {}
        self.widgets: dict[str, tuple[ttk.Combobox, ttk.Combobox, tk.StringVar]] = {}

        top = tk.Frame(self, bg=ui.BACKGROUND)
        top.pack(fill="x")
        ui.badge(top, 1).pack(side="left")
        self.read = ttk.Button(top, text="⟳  Read configs", command=self._read)
        self.read.pack(side="left", padx=6)

        middle = tk.Frame(self, bg=ui.BACKGROUND)
        middle.pack(fill="x", pady=(10, 0))
        ui.badge(middle, 2).pack(side="left", anchor="n", pady=4)
        self.table = tk.Frame(middle, bg=ui.CARD, highlightbackground=ui.BORDER, highlightthickness=1, padx=10,
                              pady=8)
        self.table.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.table.columnconfigure((1, 2), weight=1)

        actions = tk.Frame(self, bg=ui.BACKGROUND)
        actions.pack(fill="x", pady=(14, 0))
        ui.badge(actions, 3).pack(side="left")
        self.send = ttk.Button(actions, text="Send to every room", style="Big.TButton", command=self._send)
        self.send.pack(side="left", padx=6)
        ui.info(actions, "Your configs are not changed. Each room gets a copy of the config chosen for it, with the "
                         "command added to its battle end event, and the program links its clicker to this "
                         "computer. Rooms with no config chosen are left as they are.", ui.BACKGROUND).pack(
            side="left")

        self.status = tk.StringVar()
        tk.Label(self, textvariable=self.status, bg=ui.BACKGROUND, fg=ui.MUTED, anchor="w", justify="left",
                 wraplength=640).pack(fill="x", pady=(10, 0))

    def shown(self) -> None:
        config.reload()
        rooms = [(s, config.DEVICE_NAMES.get(s, s)) for s in config.DEVICES if s]
        if rooms != self.rooms:
            self.rooms = rooms
            self.found.clear()
            self._build()
        if not rooms:
            self.status.set("Pick the rooms first (emulators page).")
        elif not self.found and not self.busy:
            self._read()
        self._update()

    def _build(self) -> None:
        for child in self.table.winfo_children():
            child.destroy()
        self.widgets.clear()
        for column, text in enumerate(("Room", "Config", "Battle end event")):
            tk.Label(self.table, text=text, bg=ui.CARD, fg=ui.MUTED, font=(ui.FONT, 9, "bold")).grid(
                row=0, column=column, sticky="w", padx=(0, 8))
        ui.info(self.table, SINGLE_EVENT_TIP).grid(row=0, column=3, sticky="e")
        for index, (serial, name) in enumerate(self.rooms):
            row = 1 + index * 2
            tk.Label(self.table, text=name, bg=ui.CARD, fg=ui.TEXT, font=(ui.FONT, 10, "bold")).grid(
                row=row, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
            scenario = ttk.Combobox(self.table, state="readonly", width=28)
            scenario.grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=(6, 0))
            event = ttk.Combobox(self.table, state="readonly", width=28)
            event.grid(row=row, column=2, columnspan=2, sticky="ew", pady=(6, 0))
            note = tk.StringVar()
            tk.Label(self.table, textvariable=note, bg=ui.CARD, fg=ui.MUTED, font=(ui.FONT, 9)).grid(
                row=row + 1, column=1, columnspan=3, sticky="w")
            scenario.bind("<<ComboboxSelected>>", lambda _, s=serial: self._scenario_changed(s))
            event.bind("<<ComboboxSelected>>", lambda _: self._update())
            self.widgets[serial] = (scenario, event, note)

    def _read(self) -> None:
        adb, rooms, folder = self.app.adb, list(self.rooms), Path(self.folder.name)

        def job() -> dict:
            found = {}
            for serial, name in rooms:
                local = folder / f"{_safe(name)} configs.zip"
                if emulators.export_scenarios(adb, serial, local):
                    found[serial] = (local, [s for s in configs.read(local) if not _copy(s.name)])
            return found

        def done(found: dict) -> None:
            self.found = found
            for serial, _ in self.rooms:
                scenario, event, note = self.widgets[serial]
                listed = found.get(serial, (None, []))[1]
                scenario.configure(values=[s.name for s in listed])
                scenario.set("")
                event.configure(values=[])
                event.set("")
                note.set(f"{len(listed)} config(s) in this clicker" if serial in found else
                         "Could not read this clicker. Is it installed and opened once?")
            self.status.set("Choose a config and its battle end event for each room, then press Send.")

        self._work("Reading the configs of every room...", job, done)

    def _scenario(self, serial: str) -> configs.Scenario | None:
        listed = self.found.get(serial, (None, []))[1]
        index = self.widgets[serial][0].current()
        return listed[index] if 0 <= index < len(listed) else None

    def _event(self, serial: str) -> int | None:
        scenario = self._scenario(serial)
        index = self.widgets[serial][1].current()
        return scenario.events[index][0] if scenario and 0 <= index < len(scenario.events) else None

    def _scenario_changed(self, serial: str) -> None:
        scenario = self._scenario(serial)
        event = self.widgets[serial][1]
        event.configure(values=[f"{name}  #{event_id}" for event_id, name in scenario.events] if scenario else [])
        event.set("")
        self._update()

    def _ready(self) -> list[str]:
        return [serial for serial, _ in self.rooms if self._event(serial)]

    def _update(self) -> None:
        self.read.state(["disabled"] if self.busy or not self.rooms else ["!disabled"])
        self.send.state(["!disabled"] if self._ready() and not self.busy else ["disabled"])

    def _work(self, message: str, job, done) -> None:
        self.busy = True
        self.status.set(message)
        self._update()

        def finish(result) -> None:
            self.busy = False
            if isinstance(result, Exception):
                self.status.set(f"Error: {result}")
            else:
                done(result)
            self._update()

        self.app.run_job(job, finish)

    def _send(self) -> None:
        names = dict(self.rooms)
        plan = [(serial, self.found[serial][0], self._scenario(serial), self._event(serial))
                for serial in self._ready()]
        adb, link, folder = self.app.adb, self.app.link, Path(self.folder.name)

        def job() -> list[str]:
            sent, chosen = [], {}
            for serial, source, scenario, event in plan:
                copy = f"{scenario.name} (solo {names[serial]})"
                target = folder / f"{_safe(copy)}.zip"
                configs.prepare(source, target, scenario, event, None, serial, copy)
                if emulators.import_scenarios(adb, serial, target):
                    link(serial)
                    sent.append(names[serial])
                    chosen[serial] = copy
            if chosen:
                data = settings.load()
                data["single_configs"] = {**data["single_configs"], **chosen}
                settings.save(data)
                config.reload()
            return sent

        self._work("Preparing and sending...", job, lambda sent: self.status.set(
            f"Done on {', '.join(sent)}. Press Start on the panel: every room opens the game and plays its "
            f"'(solo ...)' copy by itself." if sent else "Nothing was sent: check that the clicker is installed."))


def _copy(name: str) -> bool:
    return name.endswith(")") and ("(solo " in name or "(dual " in name)
