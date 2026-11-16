"""Builds a single-file executable for the current OS into dist/."""
import platform
import subprocess
import sys

name = "dual-login"
subprocess.run(
    [sys.executable, "-m", "PyInstaller", "--onefile", "--console", "--name", name, "--clean", "main.py"],
    check=True,
)
print(f"\nBuilt dist/{name}{'.exe' if platform.system() == 'Windows' else ''}")
