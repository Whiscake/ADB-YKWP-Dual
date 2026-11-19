"""Builds a single-file executable for the current OS into dist/."""
import os
import platform
import subprocess
import sys

name = "dual-login"
windows = platform.system() == "Windows"
mode = ["--windowed", "--icon", "images/panel.ico"] if windows else ["--console"]
subprocess.run(
    [sys.executable, "-m", "PyInstaller", "--onefile", *mode, "--name", name, "--clean",
     "--add-data", f"images/panel*{os.pathsep}images", "main.py"],
    check=True,
)
print(f"\nBuilt dist/{name}{'.exe' if windows else ''}")
