"""Start the tray at login. The OS autostart entry itself is the setting."""

import os
import shutil
import subprocess
import sys

NAME = "Phonavigator"


def _command():
    if getattr(sys, "frozen", False):  # packaged executable
        return [sys.executable]
    launcher = shutil.which("phonavigator")  # install-linux.sh
    if launcher:
        return [launcher]
    exe = sys.executable
    if sys.platform == "win32":
        exe = exe.replace("python.exe", "pythonw.exe")  # no console window
    return [exe, "-m", "phonavigator"]


def _linux_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "autostart", "phonavigator.desktop")


def _mac_path():
    return os.path.expanduser("~/Library/LaunchAgents/nu.bruijn.phonavigator.plist")


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def enabled() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
                winreg.QueryValueEx(k, NAME)
            return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return os.path.exists(_mac_path())
    return os.path.exists(_linux_path())


def set_enabled(on: bool):
    cmd = _command()
    if sys.platform == "win32":
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            if on:
                winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
            else:
                try:
                    winreg.DeleteValue(k, NAME)
                except FileNotFoundError:
                    pass
        return
    path = _mac_path() if sys.platform == "darwin" else _linux_path()
    if not on:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if sys.platform == "darwin":
        import plistlib

        with open(path, "wb") as f:
            plistlib.dump({"Label": "nu.bruijn.phonavigator", "ProgramArguments": cmd,
                           "RunAtLoad": True}, f)
        return
    exec_line = " ".join(f'"{a}"' if " " in a else a for a in cmd)
    with open(path, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Phonavigator\n"
                "Comment=Phone as a 3D mouse\n"
                f"Exec={exec_line}\nIcon=phonavigator\nStartupNotify=false\n")
