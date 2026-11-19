"""Builds a single-file executable for the current OS into dist/."""
import os
import platform
import subprocess
import sys

name = "dual-login"
subprocess.run(
    [sys.executable, "-m", "PyInstaller", "--onefile", "--windowed", "--name", name, "--clean",
     "--icon", "images/panel.ico", "--add-data", f"images/panel*{os.pathsep}images", "main.py"],
    check=True,
)
print(f"\nBuilt dist/{name}{'.exe' if platform.system() == 'Windows' else ''}")
