"""Settings page: every value from settings.json that is worth changing, each with an explanation."""
import tkinter as tk
from tkinter import ttk

import config
import settings
import ui

GROUPS = [
    ("Emulator restart", [
        ("restart_enabled", "Restart the emulators regularly",
         "Emulators slow down after hours of play. When this is on, each emulator is restarted in turn while the "
         "other keeps playing, then its clicker and the game are started again."),
        ("restart_every_minutes", "Every (minutes)", "How long between two restart rounds."),
        ("restart_boot_timeout_seconds", "Wait for restart (s)",
         "How long a restarting emulator may take to come back before you get an alert."),
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
         "Still stuck after this long: the game is closed so both emulators go back to login and play resumes. "
         "0 never closes it."),
        ("both_at_login_window_seconds", "Both at login window (s)",
         "Two login screens reported within this time mean both emulators are waiting, so one gets the login tap."),
        ("login_retry_seconds", "Login retry (s)", "Shortest time between two login taps."),
        ("stage_clear_tap_delay_seconds", "Login tap delay (s)",
         "Wait after a stage clear before tapping login on the other emulator."),
    ]),
    ("Login button", [
        ("login_button_x", "Login button X", "Where the login button is on the game's login screen, in pixels."),
        ("login_button_y", "Login button Y", "Where the login button is on the game's login screen, in pixels."),
        ("tap_jitter_pixels", "Tap spread (pixels)", "Each tap lands a few random pixels away, like a finger."),
    ]),
    ("Stats and alerts", [
        ("points_per_clear", "Points per clear", "Used for the points per hour and the forecast."),
        ("forecast_target_datetime", "Forecast until",
         "Date for the points forecast, like 2026-12-31 23:59. Empty hides the forecast."),
        ("stats_uptime_window_hours", "Uptime window (h)", "The uptime and restart counts cover this many hours."),
        ("ntfy_url", "Phone alerts (ntfy URL)",
         "An ntfy.sh topic URL, like https://ntfy.sh/your-topic. Install the ntfy app and subscribe to the same "
         "topic to get alerts on your phone. Empty sends nothing."),
    ]),
    ("Program", [
        ("start_automatically", "Start when the program opens",
         "Start the dual play right away when you open the program."),
        ("write_logs", "Write log files",
         "Not recommended to turn off: the stats are computed from the log files, so they stay empty without "
         "them. Logs older than 3 days are deleted on their own."),
        ("game_package", "Game package", "The Android package of the game. Change it only for another game version."),
        ("port", "Port", "The port the clickers reach this program on. Applies after Stop and Start."),
    ]),
]


class SettingsPage(ui.Page):
    title = "Settings"
    help = ("Extra settings. The defaults work for most people.\n"
            "Hover the ⓘ next to a setting to see what it does, then press Save.")

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
        tk.Label(actions, textvariable=self.status, bg=ui.BACKGROUND, fg=ui.MUTED).pack(side="left", padx=6)

        scroll = ui.ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        for title, rows in GROUPS:
            card = ui.Card(scroll.inner, title)
            card.pack(fill="x", pady=(0, 8), padx=(0, 4))
            card.body.columnconfigure(3, weight=1)
            for row, (key, label, tip) in enumerate(rows):
                self._field(card.body, row, key, label, tip)
            if title == "Emulator restart":
                ttk.Button(card.body, text="Restart both now", command=app.restart_now).grid(
                    row=0, column=4, padx=(12, 0))

    def _field(self, parent, row: int, key: str, label: str, tip: str) -> None:
        default = settings.DEFAULTS[key]
        if isinstance(default, bool):
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
        for key, var in self.vars.items():
            var.set(data[key])
        self.status.set("")

    def commit(self) -> bool:
        data = settings.load()
        for key, var in self.vars.items():
            default = settings.DEFAULTS[key]
            try:
                value = var.get()
                if isinstance(default, bool):
                    value = bool(value)
                elif isinstance(default, int):
                    value = int(float(value))
                elif isinstance(default, float):
                    value = float(value)
                else:
                    value = str(value).strip()
            except (ValueError, tk.TclError):
                self.status.set(f"{self.labels[key]}: not a valid number.")
                return False
            if not isinstance(default, (bool, str)) and value < 0:
                self.status.set(f"{self.labels[key]}: can't be negative.")
                return False
            data[key] = value
        settings.save(data)
        config.reload()
        self.app.settings_saved()
        self.status.set("Saved.")
        return True
