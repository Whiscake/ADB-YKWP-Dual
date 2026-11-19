# ADB-YKWP-Dual: full guide

How the program behaves in every situation, and every setting you can change. For installing and the first start, see
the [README](README.md).

## Overview

Two emulator instances, **A** and **B**, take turns playing the game. One is **playing** a stage while the other is
**waiting** at the login screen. When the playing one clears a stage, the program:

1. waits a short delay (`stage_clear_tap_delay_seconds`)
2. taps the login button on the waiting instance, which starts playing
3. closes the game on the instance that just cleared, skipping the results animation
4. swaps the roles

A fork of Smart AutoClicker on each instance tells the program what is happening by sending **events**. The clicker
reaches the program at `127.0.0.1` through `adb reverse`, which the program sets up on Start, after a restart and
whenever the link drops, so firewalls and emulator network settings do not matter.

## Events

Each event is an ADB action with the command `<event-name> <serial>`, for example `stage-clear emulator-5554`.
**Prepare config** (the ✎ page) adds them for you. The instance can be written as its full serial, as `emulator-<port>`
or as the port number alone, so `127.0.0.1:5555`, `emulator-5554` and `5554` all mean the same instance.

| Event | Sent when | What the program does |
|---|---|---|
| `stage-clear` | Last action of the event that presses OK on the stage clear | Runs the handoff above. **Required.** |
| `login-screen` | Every 2 s while the login screen is visible | Works out which instance is waiting, and restarts play when both are at login. Recommended. |
| anything else | Whenever you add one | Logged only |

To add an event by hand, add an ADB action to the event with the command and leave address, port and token empty: the
clicker then uses the ones the program sent it.

## Situations

**Start.** For each instance the program checks that the clicker is installed and its accessibility service is on,
and that the instance has a config: the one chosen in the menu next to it on the panel, or one already running in
the clicker. If any of this is missing, nothing starts and the log says what. Otherwise it links both clickers, opens
the chosen config in each one and presses play (an open config of the same name only gets play pressed), opens the
game where it is closed, and starts coordinating. The chosen configs are kept in `dual_configs`; **Send** on the ✎ page
sets them to the new copies.

**Normal clear.** The playing instance sends `stage-clear`. Handoff runs, roles swap.

**Clear sent too early.** A `stage-clear` arriving less than `min_stage_seconds` after that instance started playing
is ignored, since it can't be a real clear. This also drops duplicate events for one clear.

**Clear from the waiting instance.** Ignored. Only the playing instance can trigger a handoff.

**First start, roles unknown.** Roles are learned from the first `stage-clear` (that instance is playing) or from a
`login-screen` on one instance (the other one must be playing).

**Both instances at the login screen.** If both send `login-screen` within `both_at_login_window_seconds` of each
other, neither is playing, so the program taps login on one of them (the one it believed was playing, otherwise A).
It does not tap again for `login_retry_seconds`.

**Only one instance at the login screen.** Nothing is tapped. That instance is simply the waiting one.

**Playing instance stops clearing stages (stuck).** A check runs every 5 seconds.
- After `stuck_after_seconds` with no clear, a warning is logged and the status turns red.
- After `alert_after_seconds`, a phone alert is sent, repeated every `alert_repeat_seconds` while still stuck.
- After `force_stop_after_seconds` (0 turns this off), the game is closed, and again every `force_stop_after_seconds`
  while still stuck. The instance goes back to the login screen, and the `login-screen` events of both instances make
  the program tap login again.
- After `rescue_restart_seconds` (0 turns this off), both instances restart together, the same way as a periodic
  restart (LDPlayer only), and a phone alert says so. When they are back, both reach login and play resumes.
- The next stage clear or login tap logs the recovery and resets everything.

**Periodic restart (LDPlayer only).** With *Restart the emulators regularly* on, every `restart_every_minutes` a
restart round starts, or right away with **Restart both now**:
1. The waiting instance restarts while the other keeps playing. Its stage clears are not handed over and the stuck
   checks are paused meanwhile.
2. The program waits for Android and for the clicker's accessibility service, links the clicker again, has it play the
   config chosen for that instance (or its last one), and opens the game. LDPlayer's own window arranging (the one of
   the multi-instance manager) then puts the windows back in place.
3. At the next handoff the other instance is the waiting one, and it restarts the same way.
4. The round ends; the next starts `restart_every_minutes` later.

While an instance restarts, the band at the top of the panel turns blue and the status shows the step it is at:
shutting down, the adb check with the time left before `restart_boot_timeout_seconds` runs out, the clicker, and the
link.

An instance that is not a running LDPlayer instance is never restarted, and the settings page will not turn the option
on for it. If a restarting instance is not back within `restart_boot_timeout_seconds`, an alert is sent. If the
clicker's accessibility service does not come back, the log asks you to turn it on by hand.

**An instance drops off adb.** The program reconnects it every 5 seconds and links its clicker again when it is back.

**Wrong token or malformed command.** Rejected and logged at most once a minute. Nothing runs.

## Settings reference

Change them on the **⚙** page; each has an ⓘ explaining it. They are stored in `settings.json`: Windows
`%APPDATA%\dual-login\`, macOS `~/Library/Application Support/dual-login/`, Linux `~/.config/dual-login/`. Delete the
file to start over with a new token.

| Key | Default | Meaning |
|---|---|---|
| `device_a`, `device_b` | picked | adb serials of the two instances (⧉ page) |
| `device_a_name`, `device_b_name` | picked | Their names, shown on the panel and in the log |
| `token` | generated | Secret the clickers send with every event; sent to them on Start |
| `port` | 8765 | Port the program listens on. Applies after Stop and Start |
| `game_package` | `com.Level5.YWP` | Android package closed after a clear |
| `login_button_x`, `login_button_y` | 525, 1187 | Where login is tapped, in device pixels. 660, 1484 on 900x1600 |
| `tap_jitter_pixels` | 8 | Random offset added to each tap. 0 turns it off |
| `ntfy_url` | blank | Your ntfy.sh topic URL for phone alerts. Blank means no alerts |
| `min_stage_seconds` | 18 | Clears sooner than this after an instance starts playing are ignored |
| `stage_clear_tap_delay_seconds` | 0.2 | Pause between a clear and the login tap |
| `stuck_after_seconds` | 40 | No clear for this long: shown as stuck |
| `alert_after_seconds` | 240 | No clear for this long: alert sent |
| `alert_repeat_seconds` | 300 | Minimum gap between repeated alerts |
| `force_stop_after_seconds` | 300 | No clear for this long: the game is closed. 0 turns it off |
| `both_at_login_window_seconds` | 6 | Two `login-screen` events this close together mean both instances are at login |
| `login_retry_seconds` | 8 | Minimum gap between login taps caused by `login-screen` events |
| `restart_enabled` | false | Restart the instances regularly (LDPlayer only) |
| `restart_every_minutes` | 120 | Time between two restart rounds |
| `restart_boot_timeout_seconds` | 240 | How long a restarting instance may take to come back |
| `rescue_restart_seconds` | 600 | No clear for this long, even after closing the game: both instances restart. 0 turns it off |
| `dual_configs` | set by Send | The config each instance plays at Start, by instance. Empty means the one already running |
| `points_per_clear` | 22 | Points one stage clear gives you |
| `forecast_target_datetime` | blank | End date like `2026-12-31 23:59` for the points forecast |
| `stats_interval_seconds` | 5 | How often the stats refresh |
| `stats_clear_sample_size` | 100 | How many recent clears the clear times are computed from |
| `stats_uptime_window_hours` | 24 | How far back uptime and restarts are counted |
| `start_automatically` | true | Start as soon as the program opens |
| `on_close` | `ask` | What closing the window does: `ask`, `tray` (keep running next to the clock) or `exit` |
| `write_logs` | true | Write the log files. The stats are computed from them |
| `tutorial_done` | false | Set once the tutorial is finished or skipped |

## Logs

`logs/coordinator.log` in the same folder. A new file starts every midnight and files older than 3 days are deleted,
so the folder stays small. It records handoffs, logins, stuck warnings, restarts, alerts, rejected requests and
unknown events. The panel's Log shows the same lines.

## Command line

The window is the normal way to run it. From source, `python3 main.py --console` runs without a window and shows a
status screen in the terminal, and `--log` prints the scrolling log instead. `GET http://127.0.0.1:<port>/health`
returns `ok active=<serial>` and is handy to check the program is up.
