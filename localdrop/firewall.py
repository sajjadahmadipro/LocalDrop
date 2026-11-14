"""Windows Firewall guidance for the port LocalDrop listens on.

Nothing here elevates itself and nothing is installed silently: `status` reads
the rules, `add`/`remove` run netsh and report what Windows said, and the exact
command line is always shown so the user can run it from an Administrator
prompt themselves.
"""

import ctypes
import os
import re
import subprocess
import sys

RULE_NAME = "LocalDrop"


def supported():
    return os.name == "nt"


def is_admin():
    if not supported():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def add_command(port):
    # profile=any: Windows labels every new Wi-Fi network Public, and a
    # private-only rule silently does nothing there, which reads as "the app is
    # broken". remoteip=localsubnet narrows the rule to devices on our own subnet
    # instead of the whole interface. Honest limit: a hotspot that hands out the
    # same subnet is still inside the rule, and LocalDrop's traffic is plain HTTP.
    return ('netsh advfirewall firewall add rule name="%s" dir=in action=allow '
            'protocol=TCP localport=%d profile=any remoteip=localsubnet enable=yes'
            % (RULE_NAME, int(port)))


def remove_command():
    return 'netsh advfirewall firewall delete rule name="%s"' % RULE_NAME


def _run(command_line):
    try:
        proc = subprocess.run(command_line, shell=True, capture_output=True,
                              text=True, timeout=20)
        return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def active_profile():
    """The category Windows is applying to the current network, or "" if unknown.

    A rule only fires on the profiles it lists, so "the rule exists" and "devices
    can reach us" are different claims. This is what separates them.
    """
    if not supported():
        return ""
    code, output = _run("netsh advfirewall show currentprofile")
    if code != 0:
        return ""
    match = re.search(r"(?im)^\s*(Domain|Private|Public)\s+Profile\s+Settings", output)
    return match.group(1).lower() if match else ""


def _rule_profiles(output):
    """Profile names the rule lists, or None when the dump cannot be read."""
    lines = re.findall(r"(?im)^[ \t]*Profiles:[ \t]*(.+)$", output)
    if not lines:
        return None
    names = set()
    for line in lines:
        for word in re.split(r"[,\s]+", line.strip().lower()):
            if word:
                names.add(word)
    return names


def program_blockers():
    """Enabled inbound BLOCK rules aimed at the Python that is running us.

    Windows writes these when a "blocked some features" prompt is cancelled, and
    a block rule overrides every allow rule: the port can be perfectly allowed
    and still dead for every device except the PC itself.
    """
    if not supported():
        return []
    exe = sys.executable.replace("/", "\\")
    code, output = _run(
        'powershell -NoProfile -Command "Get-NetFirewallRule -Direction Inbound '
        "-Action Block -Enabled True | Where-Object { $_.Name -like '*Query User*' } | "
        "ForEach-Object { $p = ($_ | Get-NetFirewallApplicationFilter).Program; "
        "if ($p -ieq '%s') { $_.Name } }\"" % exe)
    if code != 0:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def status(port):
    """(state, detail): allowed, mismatch, program-blocked, blocked, unknown,
    not-windows."""
    port = int(port)
    if not supported():
        return "not-windows", "Firewall rules are a Windows setting."
    code, output = _run('netsh advfirewall firewall show rule name="%s"' % RULE_NAME)
    lowered = output.lower()
    if "no rules match" in lowered:
        return "blocked", ("No rule named %s exists, so Windows is still deciding "
                           "whether to let devices reach LocalDrop." % RULE_NAME)
    if code != 0:
        # Usually "The request requires elevation": reading the rule list is not
        # always allowed to a normal account, and the wording is localised.
        return "unknown", output or "Windows did not answer the firewall query."
    if str(port) not in output:
        return "blocked", "A LocalDrop rule exists but does not mention port %d:\n%s" % (port, output)
    profiles = _rule_profiles(output)
    current = active_profile()
    if profiles and current and "any" not in profiles and current not in profiles:
        return "mismatch", (
            "The rule covers %s networks, but Windows is treating this network as %s, "
            "so the rule is not being applied and devices still cannot reach LocalDrop. "
            "Set this network to Private (Settings > Network & Internet > Wi-Fi > your "
            "network > Network profile type), or add a rule that covers %s networks."
            % (", ".join(sorted(profiles)), current, current))
    blockers = program_blockers()
    if blockers:
        return "program-blocked", (
            "Windows is blocking %s itself, so no allow rule can help. A firewall "
            "prompt was cancelled at some point and Windows kept a BLOCK rule, which "
            "overrides allow rules. Run scripts/firewall-open.bat as administrator to "
            "remove it, or delete these inbound block rules: %s"
            % (os.path.basename(sys.executable), "; ".join(blockers)))
    return "allowed", output


def add(port):
    if not supported():
        return False, "Firewall rules are a Windows setting."
    if not is_admin():
        return False, "Needs an Administrator prompt. Run:\n%s" % add_command(port)
    code, output = _run(add_command(port))
    return code == 0, output or ("Rule added for port %d." % int(port))


def remove():
    if not supported():
        return False, "Firewall rules are a Windows setting."
    if not is_admin():
        return False, "Needs an Administrator prompt. Run:\n%s" % remove_command()
    code, output = _run(remove_command())
    return code == 0, output or "Rule removed."


def elevate_and_add(port):
    """Ask Windows for consent, then add the rule. Returns (ok, message)."""
    if not supported():
        return False, "Firewall rules are a Windows setting."
    if is_admin():
        return add(port)
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "netsh", add_command(port).split(" ", 1)[1],
            None, 0)   # 0 = SW_HIDE: the console window would only flash
    except Exception as exc:
        return False, "Could not ask Windows for permission: %s" % exc
    if int(rc) <= 5:      # 5 = SE_ERR_ACCESSDENIED, i.e. the prompt was dismissed
        return False, ("Permission was declined. Nothing changed. The command is:\n%s"
                       % add_command(port))
    return True, "Windows was asked to allow LocalDrop from devices on this network."


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    action = argv[0] if argv and not argv[0].startswith("-") else "status"
    port = 0
    for i, arg in enumerate(argv):
        if arg == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
    if not port:
        from . import config as config_module
        port = int(config_module.Config().get("port"))
    if action == "add":
        ok, message = add(port)
    elif action == "remove":
        ok, message = remove()
    else:
        state, message = status(port)
        ok = state == "allowed"
        message = "%s (port %d): %s" % (state, port, message)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
