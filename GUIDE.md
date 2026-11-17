# ADB-YKWP-Dual: full guide

How the app behaves in every situation, and every setting you can change. For installing and first-time setup, see the [README](README.md).

## Overview

Two BlueStacks instances, **A** and **B** interchange playing the game. One is **active** (playing a stage), while the other is **idle** (sitting at the
login screen). When the active one clears a stage, the app:

1. waits a short delay (`stage_clear_tap_delay_seconds`)
2. taps the login button on the idle device, which becomes the new active device
3. force-stops the game on the device that just cleared, skipping the results animation
4. swaps the roles

A custom fork of Smart AutoClicker 1.5.1 on each device tells the app what is happening by sending **events**.

## Events

Each event is an ADB action with the command `<event-name> <serial>`, for example `stage-clear emulator-5554`.
The device can be written as its full serial, as `emulator-<port>`, or as just the port number, so `127.0.0.1:5605`, `emulator-5605` and `5605` all mean the same device.

| Event | Send it when | What the app does |
|---|---|---|
| `stage-clear` | The device clears a stage | Runs the handoff above. **Required.** |
| `login-screen` | The login button is visible | Tracks which device is idle, and restarts play when both devices are at login (see "Both at login"). Recommended. |

## Situations

**Normal clear.** The active device sends `stage-clear`. Handoff runs, roles swap.

**Clear sent too early.** A `stage-clear` arriving less than `min_stage_seconds` after that device became active is
ignored, since it can't be a real clear. This also drops duplicate events for one clear.

**Clear from the idle device.** Ignored. Only the active device can trigger a handoff.

**First start, active device unknown.** Roles are learned from the first `stage-clear` (that device is active) or from a
`login-screen` event on one device (the other must be playing). You can also start with
`--active emulator-5554` to set it directly.

**Both devices at the login screen.** If both send `login-screen` within `both_at_login_window_seconds` of each other,
neither is playing, so the app taps login on one of them (the one it believed active, otherwise device A). It will not
tap again for `login_retry_seconds`. Keep `both_at_login_window_seconds` above the repeat interval of your
`login-screen` macro action.

**Only one device at the login screen.** Nothing is tapped. That device is simply the idle one.

**Device stops clearing stages (stuck).** A check runs every 5 seconds.
- After `stuck_after_seconds` with no clear on the active device, a warning is logged.
- After `alert_after_seconds`, an alert is sent, repeated every `alert_repeat_seconds` while still stuck.
- After `force_stop_after_seconds` (0 turns this off), the game on the stuck device is force-stopped, and again every
  `force_stop_after_seconds` while still stuck. It then restarts at the login screen, and your AutoClicker's `login-screen`
  events from both devices make the app tap login on the stuck one, which resumes play.
- The next successful `stage-clear` or login tap logs the recovery and resets everything.

**Wrong token or malformed command.** Rejected (HTTP 401 or 400) and logged at most once a minute. Nothing runs.

## Settings reference (`settings.json`)

Close the app before editing. Delete the file to start setup over. Location: macOS
`~/Library/Application Support/dual-login/`, Windows `%APPDATA%\dual-login\`, Linux `~/.config/dual-login/`.

| Key | Default | Meaning |
|---|---|---|
| `device_a`, `device_b` | detected | ADB serials of the two instances |
| `token` | generated | Secret the AutoClicker must send. Must match its "Bridge token" |
| `port` | 8765 | Port the app listens on. Change it in the AutoClicker too |
| `game_package` | `com.Level5.YWP` | Android package force-stopped after a clear |
| `login_button_x`, `login_button_y` | 525, 1187 | Where login is tapped, in device pixels. Adjust for a different resolution |
| `ntfy_url` | blank | Your ntfy.sh topic URL for phone alerts. Blank means no alerts |
| `min_stage_seconds` | 18 | Clears sooner than this after a device becomes active are ignored. Set just under your fastest real stage time |
| `stage_clear_tap_delay_seconds` | 0.2 | Pause between detecting a clear and tapping login |
| `tap_jitter_pixels` | 8 | Random offset added to each tap so taps aren't pixel-identical. 0 disables |
| `stuck_after_seconds` | 40 | No clear for this long: logged as stuck |
| `alert_after_seconds` | 240 | No clear for this long: alert sent |
| `alert_repeat_seconds` | 300 | Minimum gap between repeat alerts |
| `force_stop_after_seconds` | 300 | No clear for this long: the game on that device is force-stopped so it returns to login. 0 disables |
| `both_at_login_window_seconds` | 6 | Two `login-screen` events this close together mean both devices are at login |
| `login_retry_seconds` | 8 | Minimum gap between login taps caused by `login-screen` events |
| `points_per_clear` | 18 | Points you earn per stage clear; used for the points figures on the status screen |
| `forecast_target_datetime` | blank | Optional end date, like `2026-12-01 01:00`. Adds a "points by this date" figure. Blank shows only per hour and per day |
| `stats_interval_seconds` | 5 | How often the status screen refreshes |
| `stats_clear_sample_size` | 100 | How many recent clears are used for the average, fastest and slowest times |
| `stats_uptime_window_hours` | 24 | How far back uptime is measured |

New keys are added to your file automatically the next time the app starts.

## Logs

`logs/coordinator.log` in the same folder, rotated at 5 MB with 5 backups. It records handoffs, logins, stuck
warnings, alerts, rejected requests and unknown events.
The log is written to the file only. The terminal shows the status screen (see the README); start the app with `--log` to see
the full scrolling log there instead.

## Command line

`dual-login --active emulator-5554` sets the starting active device (optional).
`dual-login --log` shows the scrolling log instead of the status screen.
`GET http://<bridge address>:<port>/health` returns `ok active=<serial>` and is handy for checking the app is up.
