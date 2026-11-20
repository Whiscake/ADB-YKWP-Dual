"""Settings page: every value from settings.json that is worth changing, each with an explanation."""
import tkinter as tk
from tkinter import ttk

import config
import emulators
import settings
import ui

LOGIN_TIP = ("Where the login button is on the game's login screen, in pixels of a 900x1600 screen. Each instance "
             "gets the same point on its own resolution, so it works at any resolution.")
CHOICES = {"on_close": [("ask", "Ask every time"), ("tray", "Minimize to tray"), ("exit", "Close completely")]}
GROUPS = [
    ("Your points", [
        ("points_per_clear", "Points per battle",
         "The points one battle (one stage clear) gives you. The game doesn't tell this program, so type it here: "
         "the points per hour and the forecast use it. You can also change it next to Points on the panel."),
        ("forecast_target_datetime", "Forecast until",
         "Date for the points forecast, like 2026-12-31 23:59. Empty hides the forecast."),
    ]),
    ("Emulator restart", [
        ("restart_enabled", "Restart the emulators regularly",
         "Emulators slow down after hours of play. When this is on, each emulator is restarted in turn while the "
         "other keeps playing (in single mode, one room at a time), then its clicker and the game are started "
         "again. Works only with LDPlayer: other emulators are never restarted."),
        ("restart_every_minutes", "Every (minutes)", "How long between two restart rounds."),
        ("restart_boot_timeout_seconds", "Wait for restart (s)",
         "How long a restarting emulator may take to come back before you get an alert."),
        ("rescue_restart_seconds", "Restart when stuck (s)",
         "When no stage is cleared for this long, even after the game was closed, both emulators restart "
         "together (in single mode, only the stuck one). 0 never does. Works only with LDPlayer."),
    ]),
    ("Timing", [
        ("min_stage_seconds", "Shortest stage (s)",
         "A stage clear sooner than this after the emulator started playing is ignored as a false alarm."),
        ("stuck_after_seconds", "Stuck after (s)",
         "With no stage clear for this long, the playing emulator is shown as stuck. Set it above your slowest "
         "stage."),
        ("alert_after_seconds", "Alert after (s)", "Still stuck after this long: a phone alert is sent (ntfy)."),
        ("alert_repeat_seconds", "Repeat alert every (s)", "How often the alert is repeated while it stays stuck."),
        ("force_stop_after_seconds", "Close game after (s)",
         "Still stuck after this long: the game is closed so both emulators go back to login and play resumes "
         "(in single mode the game is closed and opened again). 0 never closes it."),
        ("both_at_login_window_seconds", "Both at login window (s)",
         "Two login screens reported within this time mean both emulators are waiting, so one gets the login tap."),
        ("login_retry_seconds", "Login retry (s)", "Shortest time between two login taps."),
        ("stage_clear_tap_delay_seconds", "Login tap delay (s)",
         "Wait after a stage clear before tapping login on the other emulator."),
    ]),
    ("Login button", [
        ("login_button_x", "Login button X", LOGIN_TIP),
        ("login_button_y", "Login button Y", LOGIN_TIP),
        ("tap_jitter_pixels", "Tap spread (pixels)", "Each tap lands a few random pixels away, like a finger."),
    ]),
    ("Stats and alerts", [
        ("stats_uptime_window_hours", "Uptime window (h)", "The uptime and restart counts cover this many hours."),
        ("ntfy_url", "Phone alerts (ntfy URL)",
         "An ntfy.sh topic URL, like https://ntfy.sh/your-topic. Install the ntfy app and subscribe to the same "
         "topic to get alerts on your phone. Empty sends nothing."),
    ]),
    ("Program", [
        ("ask_mode", "Ask the mode when the program opens",
         "Ask every time whether to play in dual (two rooms taking turns) or single (each room plays on its own). "
         "Off, it opens in the last mode used; the ⇄ button on the panel changes it."),
        ("start_automatically", "Start when the program opens",
         "Dual mode only: start playing right away when you open the program. Single mode always waits for "
         "Start."),
        ("on_close", "Closing the window",
         "What the X of the window does: ask every time, keep the program running next to the clock "
         "(notification area), or close it completely."),
        ("write_logs", "Write log files",
         "Not recommended to turn off: the stats are computed from the log files, so they stay empty without "
         "them. The log files are deleted when you close the program."),
        ("game_package", "Game package", "The Android package of the game. Change it only for another game version."),
        ("port", "Port", "The port the clickers reach this program on. Applies after Stop and Start."),
    ]),
]


class SettingsPage(ui.Page):
    title = "Settings"
    help = ("1  Type the points one battle gives you: the stats use it.\n"
            "2  The emulators restart by themselves every 20 hours, one at a time while the other plays, to clear the "
            "lag (LDPlayer only). Change it under Emulator restart.\n"
            "3  The other settings work as they are for most people. Hover the ⓘ next to one to see what it does.\n"
            "4  Press Save.")

    def __init__(self, app) -> None:
        super().__init__(app)
        self.vars: dict[str, tk.Variable] = {}
        self.labels: dict[str, str] = {}

        actions = tk.Frame(self, bg=ui.BACKGROUND)
        actions.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(actions, text="Save", style="Big.TButton", command=self.commit).pack(side="right")
        ttk.Button(actions, text="Log folder", command=lambda: app.open_path(settings.DATA_DIR / "logs")).pack(
            side="left")
        ttk.Button(actions, text="Settings file", command=lambda: app.open_path(settings.SETTINGS_FILE)).pack(
            side="left", padx=6)
        self.status = tk.StringVar()
        self.status_label = tk.Label(actions, textvariable=self.status, bg=ui.BACKGROUND, fg=ui.MUTED,
                                     font=(ui.FONT, 10, "bold"))
        self.status_label.pack(side="left", padx=6)
        self.status_job = None

        scroll = ui.ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        for title, rows in GROUPS:
            card = ui.Card(scroll.inner, title)
            card.pack(fill="x", pady=(0, 8), padx=(0, 4))
            card.body.columnconfigure(3, weight=1)
            for row, (key, label, tip) in enumerate(rows):
                self._field(card.body, row, key, label, tip)
            if title == "Emulator restart":
                self.restart = ttk.Button(card.body, text="Restart both now", command=self._restart_now)
                self.restart.grid(row=0, column=4, padx=(12, 0))
        bridge = ui.Card(scroll.inner, "Bridge (only for a clicker set up by hand)")
        bridge.pack(fill="x", pady=(0, 8), padx=(0, 4))
        note = tk.Label(bridge.body, text="You don't need these: Send and Start set them in the clicker for you. "
                                          "Don't type them in a config.", bg=ui.CARD, fg=ui.MUTED,
                        font=(ui.FONT, 9), anchor="w", justify="left")
        note.pack(fill="x", pady=(0, 4))
        bridge.row("Address", "127.0.0.1", copy=True)
        self.bridge_port = bridge.row("Port", copy=True)
        self.bridge_token = bridge.row("Token", copy=True, secret=True)

    def _field(self, parent, row: int, key: str, label: str, tip: str) -> None:
        default = settings.DEFAULTS[key]
        if key in CHOICES:
            var = tk.StringVar()
            tk.Label(parent, text=label, bg=ui.CARD, fg=ui.TEXT, font=(ui.FONT, 9)).grid(row=row, column=0,
                                                                                        sticky="w", pady=3)
            ttk.Combobox(parent, textvariable=var, state="readonly", width=20,
                         values=[text for _, text in CHOICES[key]]).grid(row=row, column=1, sticky="w", padx=8,
                                                                         pady=3)
        elif isinstance(default, bool):
            var = tk.BooleanVar()
            ttk.Checkbutton(parent, text=label, variable=var).grid(row=row, column=0, columnspan=2, sticky="w",
                                                                    pady=3)
        else:
            var = tk.StringVar()
            tk.Label(parent, text=label, bg=ui.CARD, fg=ui.TEXT, font=(ui.FONT, 9)).grid(row=row, column=0,
                                                                                        sticky="w", pady=3)
            ttk.Entry(parent, textvariable=var, width=40 if isinstance(default, str) else 10).grid(
                row=row, column=1, sticky="w", padx=8, pady=3)
        ui.info(parent, tip).grid(row=row, column=2, sticky="w")
        self.vars[key] = var
        self.labels[key] = label

    def shown(self) -> None:
        data = settings.load()
        self.bridge_port.set(str(data["port"]))
        self.bridge_token.set(data["token"])
        for key, var in self.vars.items():
            var.set(dict(CHOICES[key]).get(data[key], data[key]) if key in CHOICES else data[key])
        self._say("")
        self._restart_state()

    def commit(self) -> bool:
        data = settings.load()
        for key, var in self.vars.items():
            default = settings.DEFAULTS[key]
            try:
                value = var.get()
                if key in CHOICES:
                    value = next((code for code, text in CHOICES[key] if text == value), default)
                elif isinstance(default, bool):
                    value = bool(value)
                elif isinstance(default, int):
                    value = int(float(value))
                elif isinstance(default, float):
                    value = float(value)
                else:
                    value = str(value).strip()
            except (ValueError, tk.TclError):
                self._say(f"✗  {self.labels[key]}: not a valid number.", ui.RED)
                return False
            if not isinstance(default, (bool, str)) and value < 0:
                self._say(f"✗  {self.labels[key]}: can't be negative.", ui.RED)
                return False
            data[key] = value
        rooms = data["single_devices"] if data["mode"] == "single" else (data["device_a"], data["device_b"])
        refused = data["restart_enabled"] and not settings.load()["restart_enabled"] and not all(
            emulators.restartable(s) for s in rooms if s)
        if refused:
            data["restart_enabled"] = False
            self.vars["restart_enabled"].set(False)
        settings.save(data)
        config.reload()
        self.app.settings_saved()
        if refused:
            self._say("✓  Saved, but restart stays off: it works only with open LDPlayer instances.", ui.ORANGE)
            return True
        self._say("✓  Saved", ui.GREEN_DARK)
        return True

    def _say(self, text: str, color: str = ui.MUTED) -> None:
        self.status.set(text)
        self.status_label.configure(fg=color)
        if self.status_job:
            self.after_cancel(self.status_job)
            self.status_job = None
        if text and color != ui.RED:
            self.status_job = self.after(4000, lambda: self._say(""))

    def _restart_now(self) -> None:
        if self.app.restart_now():
            self._restart_state()

    def _restart_state(self) -> None:
        coordinator = self.app.coordinator
        busy = coordinator is not None and (coordinator.restart_requested or bool(coordinator.restart_pending))
        idle = "Restart all now" if config.MODE == "single" else "Restart both now"
        self.restart.configure(text="⟳  Restarting..." if busy else idle)
        self.restart.state(["disabled"] if busy else ["!disabled"])
        if busy and self.winfo_ismapped():
            self.after(1000, self._restart_state)
