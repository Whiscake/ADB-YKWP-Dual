"""Clicker page: turns one of your clicker configs into a dual-ready copy and installs it on both emulators."""
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import config
import configs
import emulators
import ui

NONE = "(none)"
FROM_PC = "A file on this PC..."


class ConfigPage(ui.Page):
    title = "Clicker config"
    help = ("1  Choose where your clicker config is: in one of the emulators, or a backup file on this PC.\n"
            "2  Choose the config, then the event that presses OK after a stage clear and the one that sees "
            "the login screen.\n"
            "3  Press Send: both emulators get a ready copy. In each clicker, start the copy called "
            "'(dual ...)'.")

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
            sent = []
            for serial, name in devices:
                target = Path(self.folder.name) / f"{_safe(scenario.name)} - dual {_safe(name)}.zip"
                configs.prepare(source, target, scenario, clear, login, serial, f"{scenario.name} (dual {name})")
                if emulators.import_scenarios(adb, serial, target):
                    link(serial)
                    sent.append(name)
            return sent

        self._work("Preparing and sending...", job, lambda sent: self.status.set(
            f"Done on {', '.join(sent)}. In each clicker, start '{scenario.name} (dual ...)'." if sent else
            "Nothing was sent: check that the clicker is installed on both emulators."))


def _safe(text: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in text).strip()
