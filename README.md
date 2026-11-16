# ADB-YKWP-Dual

Runs two BlueStacks instances of Yo-kai Watch Puni Puni so that only one plays a stage at a time. When the active
instance clears a stage, the idle one is logged in and the game on the instance that just cleared is stopped to skip
the results animation, roughly halving the time per stage.

Smart AutoClicker on each instance detects the stage clear and reports it to this app over ADB. The app handles the
handoff, watches for a stuck instance, and can send phone alerts through [ntfy.sh](https://ntfy.sh).

## Install

See [INSTALL.md](INSTALL.md). Download the app for your system from the
[latest release](https://github.com/Whiscake/ADB-YKWP-Dual/releases/latest); no Python required.
