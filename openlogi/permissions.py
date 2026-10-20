"""Install the hidraw udev rule with polkit (needed by AppImage / source installs)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .models import ALL_PIDS

RULE_NAME = "70-openlogi-headset.rules"
RULE = (
    "# OpenLogi Headset: let the logged-in user talk HID++ to supported Logitech headsets.\n"
    'KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="046d", '
    f'ATTRS{{idProduct}}=="{"|".join(f"{pid:04x}" for pid in ALL_PIDS)}", TAG+="uaccess"\n'
)


def rule_installed() -> bool:
    return any((Path(d) / RULE_NAME).exists() for d in ("/etc/udev/rules.d", "/usr/lib/udev/rules.d"))


def install_rule() -> tuple[bool, str]:
    """Copy the rule to /etc/udev/rules.d via pkexec and re-trigger hidraw devices."""
    if shutil.which("pkexec") is None:
        return False, "pkexec not found"
    with tempfile.NamedTemporaryFile("w", suffix=".rules", delete=False) as fh:
        fh.write(RULE)
        tmp = fh.name
    script = (
        f'install -m644 "{tmp}" /etc/udev/rules.d/{RULE_NAME} && '
        "udevadm control --reload-rules && "
        "udevadm trigger --subsystem-match=hidraw --action=change"
    )
    try:
        res = subprocess.run(["pkexec", "sh", "-c", script], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return res.returncode == 0, (res.stderr or res.stdout).strip()
