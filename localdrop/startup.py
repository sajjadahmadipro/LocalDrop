"""The Windows sign-in entry, shared by the launcher window and the web settings.

Only HKCU\\Software\\...\\Run is touched: no service, no scheduled task, no
machine-wide key, and nothing at all until the user asks for it.
"""

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "LocalDrop"


def supported():
    return os.name == "nt"


def command():
    """Command line that reopens the launcher, iconified, with the server up."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.py")
    exe = sys.executable or ""
    head, tail = os.path.split(exe)
    if tail.lower() == "python.exe":
        windowless = os.path.join(head, "pythonw.exe")
        if os.path.exists(windowless):
            exe = windowless
    if not exe or not os.path.exists(script):
        return ""
    return '"%s" "%s" --minimized --start' % (exe, script)


def _open(write=False):
    import winreg
    access = winreg.KEY_READ | (winreg.KEY_SET_VALUE if write else 0)
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)


def current():
    """The stored command line, or an empty string when nothing is registered."""
    if not supported():
        return ""
    try:
        import winreg
        key = _open()
        try:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
        finally:
            key.Close()
    except OSError:
        return ""


def is_enabled():
    # An exact match matters: an entry left behind by a moved or deleted copy of
    # LocalDrop would show as "on" while never starting this one.
    wanted = command()
    return bool(wanted) and current() == wanted


def _reason(exc):
    return getattr(exc, "strerror", None) or str(exc)


def enable():
    if not supported():
        return False, "Sign-in start is only available on Windows."
    wanted = command()
    if not wanted:
        return False, "Could not work out how to relaunch LocalDrop on this machine."
    try:
        import winreg
        key = _open(write=True)
        try:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, wanted)
        finally:
            key.Close()
    except OSError as exc:
        return False, "Windows refused the sign-in entry: %s" % _reason(exc)
    return True, wanted


def disable():
    if not supported():
        return False, "Sign-in start is only available on Windows."
    try:
        key = _open(write=True)
        try:
            import winreg
            winreg.DeleteValue(key, VALUE_NAME)
        finally:
            key.Close()
    except FileNotFoundError:
        return True, ""            # already absent, which is what was asked for
    except OSError as exc:
        return False, "Windows refused to remove the sign-in entry: %s" % _reason(exc)
    return True, ""


def apply(enabled):
    return enable() if enabled else disable()
