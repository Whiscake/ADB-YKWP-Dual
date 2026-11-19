"""Entry point: runs first-time setup, then starts the handoff server."""
import sys

import settings


def run() -> None:
    if not {"--console", "--log"} & set(sys.argv):
        import gui
        gui.run()
        return
    first_run = not settings.SETTINGS_FILE.exists()
    cfg = settings.ensure_setup()
    if first_run or "--log" in sys.argv:
        settings.print_app_instructions(cfg)
    if first_run:
        input("Enter these into Smart AutoClicker, then press Enter to start. ")
    import handoff
    handoff.main()


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
    except SystemExit as exc:
        if exc.code:
            print(exc.code)
            if getattr(sys, "frozen", False):
                input("Press Enter to close.")
