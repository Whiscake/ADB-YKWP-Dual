# dual-login: install guide

Runs two BlueStacks instances of Yo-kai Watch Puni Puni so only one plays at a time. When the active one clears a
stage, this app logs in the idle one and force-stops the one that just cleared.

## What you need
- Two BlueStacks instances with the game
- The Smart AutoClicker build with the ADB action (`smartautoclicker-debug.apk` from the release page), installed in each instance, with your macro loaded
- In **each** BlueStacks instance: Settings -> Advanced -> turn on **Android Debug Bridge (ADB)**
- Nothing else. No Python, no adb install (the app offers to download Google's adb for you).

## Install
1. Open the latest release: <https://github.com/Whiscake/ADB-YKWP-Dual/releases/latest> and download the file for your system:
   - Mac: `dual-login-macos` | Windows: `dual-login-windows.exe` | Linux: `dual-login-linux`
2. Run it.
   - **Windows:** double-click. If SmartScreen warns, choose *More info -> Run anyway*. Allow it through the firewall
     (Private networks) when asked, or the emulators can't reach it.
   - **Mac:** open Terminal, then `chmod +x ~/Downloads/dual-login-macos && xattr -d com.apple.quarantine ~/Downloads/dual-login-macos && ~/Downloads/dual-login-macos`
   - **Linux:** `chmod +x dual-login-linux && ./dual-login-linux`

## First run (once, about 2 minutes)
1. If it can't find `adb` it asks to download it. Say yes.
2. Start both BlueStacks instances. The app finds them and saves their names (e.g. `emulator-5554`).
3. It prints a box like:

       Bridge address : 192.168.1.20
       Port           : 8765
       Bridge token   : k3j...
       Commands       : stage-clear emulator-5554 / stage-clear emulator-5574

4. In Smart AutoClicker, on each device, add an **ADB action** at the point where a stage is cleared:
   enter that bridge address, port, and token, and the command for **that** device.
5. Press Enter in the app. Leave the window open while playing.

Next time just run it again: it remembers everything. Your settings and logs are in:
- Mac `~/Library/Application Support/dual-login/` | Windows `%APPDATA%\dual-login\` | Linux `~/.config/dual-login/`

## Optional events (same ADB action, different command)
`login-screen <serial>`, `login-success <serial>`, `hold <serial>` (needs a person; pauses stuck detection),
anything else is just logged.

## Settings (`settings.json`, edit while the app is closed)
| key | meaning |
|---|---|
| `login_button_x`, `login_button_y` | where the login button is tapped (default is for the original device resolution) |
| `ntfy_url` | your [ntfy.sh](https://ntfy.sh) topic URL for phone alerts; blank = no alerts |
| `port` | change if 8765 is taken (update the AutoClicker to match) |
| `game_package` | game's Android package, `com.Level5.YWP` |

To start over, delete `settings.json`.

## Troubleshooting
- *"Found 0 devices"*: ADB not enabled in BlueStacks, or the instance isn't running.
- *Log says "wrong bridge token"*: token in the AutoClicker doesn't match the one the app printed.
- *Nothing happens on a clear*: the emulator can't reach this computer; check the bridge address (LAN IP) and the firewall.
- Security: the token stops other devices on your network from tapping your emulators. Don't share it, and don't run this on untrusted Wi-Fi.
