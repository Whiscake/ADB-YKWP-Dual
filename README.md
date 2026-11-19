# ADB-YKWP-Dual

Runs two emulator instances of YWPP so that only one plays a stage at a time. When the playing instance clears a
stage, the waiting one is logged in and the game on the instance that just cleared is closed to skip the results
animation, roughly halving the time per stage.

Smart AutoClicker on each instance sees what is happening on screen and tells this program. The program does the
handoff, watches for a stuck instance, restarts laggy emulators, and can send phone alerts through
[ntfy.sh](https://ntfy.sh).

Contents: [What you need](#what-you-need) · [Install](#install) · [First start: the tutorial](#first-start-the-tutorial) ·
[The panel](#the-panel) · [Tray](#tray) · [Settings](#settings) · [Keep your computer awake](#keep-your-computer-awake) ·
[Troubleshooting](#troubleshooting)

## What you need

- Two emulator instances with the game installed. **LDPlayer 9** and **BlueStacks 5** are found automatically.
  The periodic restart works with LDPlayer only.
- **ADB turned on in each instance.**
  - LDPlayer: **Settings → Other settings → ADB debugging → Open local connection**.
  - BlueStacks: **Settings → Advanced → Android Debug Bridge (ADB)**.
- The Smart AutoClicker build with the ADB action, installed in each instance, with your own config that plays the
  stages. The program can install it for you, or download it yourself: [smartautoclicker.apk](https://github.com/barone138/ykwp-clicker-library/releases/latest/download/smartautoclicker.apk).
  Its accessibility service must be on in each instance (Android **Settings → Accessibility**).

Nothing else: no Python and no adb install are needed for the release. The program offers to download adb itself.

## Install

**Windows:** download `dual-login-windows.exe` from the
[latest release](https://github.com/Whiscake/ADB-YKWP-Dual/releases/latest) and double-click it. It opens a window;
nothing else is needed.

- **Windows protected your PC (SmartScreen):** the program is not code-signed. Click **More info**, then **Run anyway**.

**Mac and Linux, or from source:** install **Python 3.10 or newer** from <https://www.python.org/downloads/>, download
the code (**Code → Download ZIP**), open a terminal in that folder and run `python3 main.py`. On Windows the same works
with `py main.py`.

To build the Windows program yourself: `py -m pip install pyinstaller`, then `py build.py`. It is written to `dist/`.

## First start: the tutorial

The first time, the program asks whether you want the tutorial. **Skip tutorial** goes straight to the panel; you can
start the tutorial again any time with the **?** button. The green band at the top always says what to do on the
screen you are on, and the green numbered circles match its steps.

### Step 1: Emulators

1. Open both instances with ADB turned on, then press **Refresh**. Every instance is listed by its name, with its state
   and whether the clicker is installed.
2. Click two instances to tick them. These are the two that take turns.
3. If the Clicker column says *missing*, press **Install clicker**.

### Step 2: Clicker config

You make one normal config that plays the stages. The program then makes a copy for each instance with the commands it
needs, and installs it.

1. Choose where your config is: in the clicker of one of the instances, or a backup `.zip` on this computer.
2. Choose the config, then two of its events:
   - **Stage clear event:** the event that presses OK on the stage clear screen. It gets a `stage-clear` command as its
     last action, so the other instance starts as soon as this one is done.
   - **Login screen event:** the event that sees the game's login (title) screen. Its own clicks are removed: it only
     reports `login-screen` every 2 seconds, and the program taps login when it is that instance's turn.
3. Press **Send to both emulators**. Each clicker gets a copy called *(your config) (dual (instance))*.
4. Back on the panel, the menu next to each instance shows the config it plays: Send picks the new copy for you. You can
   change it there any time, or leave **Config already running** to keep the one open in the clicker.

The program also links both clickers to this computer through adb, so there is no address or token to type.

### Step 3: Settings

Type the points one battle gives you (the stats use it). The other settings work as they are for most people; each has
an ⓘ that explains it. See [Settings](#settings).

## The panel

- **▶ Start / ■ Stop** starts and stops everything. With *Start when the program opens* on, it starts by itself.
  Start does the rest on its own: in each instance it opens the chosen config in the clicker and presses play (or just
  presses play if it is already open), opens the game if it is closed, then coordinates the two. If an instance has
  no clicker, its accessibility service is off, or it has no config chosen and none running, nothing starts and the
  log says why.
- The square buttons open the **⧉ emulators**, the **✎ clicker config**, the **⚙ settings** and the **? tutorial**.
- **Bridge:** address, port and token (only needed if you set up a clicker by hand; the token shows as dots, click the
  eye to see it, **Copy** always copies it), and for each instance: whether it is playing, waiting or restarting, the
  last message its clicker sent, how long the emulator has been on, how many times it was restarted in the uptime
  window, and the menu with the config it plays. The menu reads the configs saved in that clicker.
- **Stats:** clears this run, clear times, uptime (time since Start, with the time lost while stuck), points per hour
  and per day, and the forecast. The points per battle can be changed right next to *Points*.
- **Log:** what the program is doing. The same lines are saved in the log files.
- While an instance restarts, the band at the top turns blue and the status shows each step; while the program waits
  for the instance to answer on adb again, it shows the time left.

## Tray

On Windows the program also has an icon in the notification area, next to the clock. A click on it opens the panel;
the right click has **Open panel**, **Start / Stop**, **Restart both now** and **Exit**. Hovering it shows the status.

Closing the window asks whether to keep the program running there or close it completely, with **Don't ask again** to
remember the answer. *Closing the window* in the settings changes it later.

## Settings

Everything is changed from the **⚙** page. Each setting has an ⓘ that says what it does. The most useful ones:

| Setting | Default | What it does |
|---|---|---|
| Points per battle | 22 | Points one stage clear gives you; used for points per hour and the forecast |
| Forecast until | blank | A date like `2026-12-31 23:59` to forecast your points by |
| Restart the emulators regularly | off | Restarts each instance in turn while the other plays, then starts its clicker and the game again. LDPlayer only |
| Every (minutes) | 120 | Time between two restart rounds |
| Restart both when stuck (s) | 600 | No stage cleared for this long, even after closing the game: both instances restart together. 0 never. LDPlayer only |
| Stuck after (s) | 40 | No stage clear for this long: shown as stuck. Set it above your slowest stage |
| Close game after (s) | 300 | Still stuck after this long: the game is closed, both instances go back to login and play resumes. 0 never |
| Login button X / Y | 525, 1187 | Where login is tapped, in device pixels. 660, 1484 on a 900x1600 instance |
| Phone alerts (ntfy URL) | blank | An ntfy.sh topic for alerts on your phone |
| Closing the window | Ask every time | What the window's X does: ask, minimize to the tray, or close completely |
| Write log files | on | Not recommended to turn off: the stats are computed from the logs. Logs older than 3 days are deleted |

The values are kept in `settings.json`, in `%APPDATA%\dual-login\` on Windows, `~/Library/Application Support/dual-login/`
on Mac and `~/.config/dual-login/` on Linux. The **Settings file** and **Log folder** buttons open them. The full list
is in [GUIDE.md](GUIDE.md#settings-reference).

## Keep your computer awake

The program only works while your computer is awake: the screen can turn off, the computer must not sleep.

- **Windows:** **Settings → System → Power & battery → Screen, sleep, & hibernate timeouts**, set **Sleep** to **Never**.
- **Mac:** start it with `caffeinate -i -s python3 main.py`, and keep the Mac on power.
- **Linux:** start it with `systemd-inhibit --what=sleep python3 main.py`.

## Troubleshooting

- **No instance found:** ADB is off in the instance, or the instance is closed. Turn ADB on, then press **Refresh**.
- **Start does nothing and the log says "not started":** the line says why. Usually the clicker's accessibility service
  is off in that instance (turn it on in Android **Settings → Accessibility**), or no config is chosen next to it and
  none is running.
- **Install clicker fails, or the instance says the app is not compatible:** a clicker from another source is
  installed with a different signature. Export your configs from it, uninstall it, then install again.
- **Nothing happens on a stage clear:** check the last message of each instance on the panel. *No message yet* means the
  clicker is not running the dual copy, or it lost the link: check the config in the menu next to the instance, then
  press Stop and Start.
- **The log says "wrong bridge token":** the clicker has another token. Press Stop and Start, which sends it again.
- **Login is tapped in the wrong place:** change *Login button X / Y* in the settings.
- **"Stuck" while a stage is still going:** raise *Stuck after* above your slowest stage.
- **The clicker stops after closing the instance's window:** LDPlayer closes the apps when you close an instance's tab,
  and Android then leaves the clicker's accessibility service on but not running. Restart the instance.

Every situation, including what happens when something goes wrong, is covered in [GUIDE.md](GUIDE.md).

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md). You may use, modify and share this software for any non-commercial purpose,
and it comes with no warranty or liability. You may not sell it or use it commercially, including modified versions.
