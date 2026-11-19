"""Emulators page: pick the two instances to run and install the clicker on them."""
import tkinter as tk
import urllib.request
from tkinter import ttk

import emulators
import settings
import ui

STATE_COLORS = {"ready": "#2E7D32", "not attached": ui.ORANGE, "ADB disabled": "#C62828", "off": "#9E9E9E"}
STATE_TEXT = {"ready": "ready", "not attached": "not connected", "ADB disabled": "ADB is off", "off": "closed"}
APK_FILE = settings.DATA_DIR / "smartautoclicker-debug.apk"
TICKED, UNTICKED = "☑", "☐"


class EmulatorsPage(ui.Page):
    title = "Emulators"
    help = ("1  Open both emulator instances and turn on ADB in each instance's settings, then press Refresh.\n"
            "2  Click two instances to tick them: these are the two that take turns playing.\n"
            "3  If the Clicker column says missing, press Install clicker.")

    def __init__(self, app) -> None:
        super().__init__(app)
        self.emulators: dict[str, emulators.Emulator] = {}
        self.ticked: list[str] = []
        self.busy = False

        top = tk.Frame(self, bg=ui.BACKGROUND)
        top.pack(fill="x", pady=(0, 6))
        ui.badge(top, 1).pack(side="left")
        ttk.Button(top, text="⟳  Refresh", command=self.refresh).pack(side="left", padx=6)
        self.show_closed = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Show closed instances", variable=self.show_closed, style="Page.TCheckbutton",
                        command=self._fill).pack(side="left", padx=6)

        middle = tk.Frame(self, bg=ui.BACKGROUND)
        middle.pack(fill="both", expand=True)
        ui.badge(middle, 2).pack(side="left", anchor="n", pady=4)
        table_box = tk.Frame(middle, bg=ui.BACKGROUND)
        table_box.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.table = ttk.Treeview(table_box, columns=("use", "room", "state", "clicker"), show="headings",
                                  selectmode="none", height=6)
        self.table.heading("use", text="Use")
        self.table.heading("room", text="Instance", anchor="w")
        self.table.heading("state", text="State", anchor="w")
        self.table.heading("clicker", text="Clicker", anchor="w")
        self.table.column("use", width=50, minwidth=50, anchor="center", stretch=False)
        self.table.column("room", width=220, minwidth=120)
        self.table.column("state", width=120, minwidth=90)
        self.table.column("clicker", width=100, minwidth=80)
        for state, color in STATE_COLORS.items():
            self.table.tag_configure(state, foreground=color)
        self.table.tag_configure("ticked", background=ui.GREEN_LIGHT)
        bar = ttk.Scrollbar(table_box, command=self.table.yview)
        self.table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<Button-1>", self._click)

        bottom = tk.Frame(self, bg=ui.BACKGROUND)
        bottom.pack(fill="x", pady=(8, 0))
        ui.badge(bottom, 3).pack(side="left")
        self.install = ttk.Button(bottom, text="Install clicker", command=self._install)
        self.install.pack(side="left", padx=6)
        ui.info(bottom, "Downloads the clicker app and installs it on the ticked instances. Instances that already have "
                        "it get updated, their configs are kept.", ui.BACKGROUND).pack(side="left")
        self.use = ttk.Button(bottom, text="Use these two instances", command=self.commit)
        self.use.pack(side="right")

        self.status = tk.StringVar()
        tk.Label(self, textvariable=self.status, bg=ui.BACKGROUND, fg=ui.MUTED, anchor="w", justify="left",
                 wraplength=640).pack(fill="x", pady=(8, 0))
        self._update_buttons()

    def shown(self) -> None:
        data = settings.load()
        self.ticked = [s for s in (data["device_a"], data["device_b"]) if s]
        self.use.pack_forget() if self.app.tutorial else self.use.pack(side="right")
        self.refresh()

    def _click(self, event) -> None:
        serial = self.table.identify_row(event.y)
        if not serial or self.busy:
            return
        if serial in self.ticked:
            self.ticked.remove(serial)
        else:
            self.ticked = (self.ticked + [serial])[-2:]
        self._fill()

    def _update_buttons(self) -> None:
        ready = [s for s in self.ticked if s in self.emulators and self.emulators[s].attached]
        self.install.state(["!disabled"] if ready and not self.busy else ["disabled"])
        self.use.state(["!disabled"] if len(self.ticked) == 2 and not self.busy else ["disabled"])

    def _work(self, message: str, job, done) -> None:
        self.busy = True
        self.status.set(message)
        self._update_buttons()

        def finish(result) -> None:
            self.busy = False
            if isinstance(result, Exception):
                self.status.set(f"Error: {result}")
            else:
                done(result)
            self._update_buttons()

        self.app.run_job(job, finish)

    def refresh(self) -> None:
        adb = self.app.adb

        def job():
            found = emulators.discover(adb)
            for emulator in found:
                emulators.connect(adb, emulator)
                if emulator.attached:
                    emulator.clicker = emulators.has_clicker(adb, emulator.serial)
            return found

        def done(found) -> None:
            self.emulators = {e.serial: e for e in found}
            self._fill()
            running = sum(e.running for e in found)
            self.status.set(f"{running} instance(s) open." if running else
                            "No open instance found. Open your two emulator instances, then press Refresh.")

        self._work("Looking for emulator instances...", job, done)

    def _fill(self) -> None:
        self.table.delete(*self.table.get_children())
        for serial, emulator in self.emulators.items():
            if not self.show_closed.get() and not emulator.running and serial not in self.ticked:
                continue
            clicker = "-" if emulator.clicker is None else "installed" if emulator.clicker else "missing"
            tags = (emulator.state, "ticked") if serial in self.ticked else (emulator.state,)
            self.table.insert("", "end", iid=serial, tags=tags,
                              values=(TICKED if serial in self.ticked else UNTICKED, emulator.name,
                                      STATE_TEXT.get(emulator.state, emulator.state), clicker))
        self._update_buttons()

    def commit(self) -> bool:
        data = settings.load()
        if len(self.ticked) != 2:
            if data["device_a"] and data["device_b"] and not self.ticked:
                return True
            self.status.set("Tick exactly two instances first.")
            return False
        names = [self.emulators[s].name if s in self.emulators else s for s in self.ticked]
        changed = set(self.ticked) != {data["device_a"], data["device_b"]}
        data["device_a"], data["device_b"] = self.ticked
        data["device_a_name"], data["device_b_name"] = names
        settings.save(data)
        self.status.set(f"Saved: {names[0]} and {names[1]}.")
        if changed:
            self.app.pair_changed()
        return True

    def _install(self) -> None:
        targets = [self.emulators[s] for s in self.ticked if s in self.emulators and self.emulators[s].attached]
        adb = self.app.adb

        def job():
            if not APK_FILE.exists():
                urllib.request.urlretrieve(emulators.CLICKER_APK_URL, APK_FILE)
            return [(e.name, emulators.install_apk(adb, e.serial, APK_FILE)) for e in targets]

        def done(results) -> None:
            for emulator in targets:
                emulator.clicker = emulators.has_clicker(adb, emulator.serial)
            self._fill()
            self.status.set(" | ".join(f"{name}: {outcome or 'done'}" for name, outcome in results))

        self._work("Downloading and installing the clicker...", job, done)
