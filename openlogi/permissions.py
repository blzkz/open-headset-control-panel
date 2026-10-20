"""Install the hidraw udev rule with polkit (needed by AppImage / source installs)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .models import ALL_PIDS

RULE_NAME = "70-openlogi-headset.rules"
LOGITECH_VID = 0x046D


def supported_ids() -> dict[int, list[int]]:
    """USB vendor id -> product ids of every headset the app can drive."""
    from .drivers import REGISTRY

    ids: dict[int, set[int]] = {LOGITECH_VID: set(ALL_PIDS)}
    for cls in REGISTRY.drivers:
        ids.setdefault(cls.VENDOR, set()).update(cls.PIDS)
    return {vid: sorted(pids) for vid, pids in sorted(ids.items())}


def udev_rule() -> str:
    from .drivers.session import VENDOR_NAMES

    lines = ["# OpenLogi Headset: let the logged-in user configure supported headsets over hidraw.",
             "# Generated from openlogi/models.py and openlogi/drivers (python3 -m openlogi.permissions)."]
    for vid, pids in supported_ids().items():
        lines.append(f"# {VENDOR_NAMES.get(vid, f'{vid:04x}')}")
        lines.append(f'KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{{idVendor}}=="{vid:04x}", '
                     f'ATTRS{{idProduct}}=="{"|".join(f"{pid:04x}" for pid in pids)}", TAG+="uaccess"')
    return "\n".join(lines) + "\n"


RULE = udev_rule()


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


def modaliases() -> list[str]:
    return [f"usb:v{vid:04X}p{pid:04X}*" for vid, pids in supported_ids().items() for pid in pids]


if __name__ == "__main__":
    import re

    packaging = Path(__file__).resolve().parents[1] / "packaging"
    (packaging / RULE_NAME).write_text(RULE)
    meta = packaging / "io.github.blzkz.OpenLogiHeadset.metainfo.xml"
    block = "".join(f"    <modalias>{m}</modalias>\n" for m in modaliases())
    meta.write_text(re.sub(r"  <provides>\n.*?  </provides>", f"  <provides>\n{block}  </provides>",
                           meta.read_text(), flags=re.S))
    print(f"wrote {packaging / RULE_NAME} and the modaliases in {meta.name}")
