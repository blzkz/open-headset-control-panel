"""Generate openlogi/drivers/specs.py (ids, capabilities, EQ data) from the golden file.

    python3 tools/gen_driver_specs.py

The golden file itself comes from HeadsetControl's C++ (tools/golden/), so this keeps
the static per-device data identical to upstream without hand copying.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "data" / "headsetcontrol_golden.json.gz"
# Band layout read from the headset at run time; the golden file only has what the script replied.
EQ_FROM_DEVICE = {"Logitech G PRO X 2 LIGHTSPEED"}
OUT = ROOT / "openlogi" / "drivers" / "specs.py"


def main() -> None:
    with gzip.open(GOLDEN, "rt") as fh:
        devices = json.load(fh)
    lines = [
        '"""Static per-device data generated from HeadsetControl (tools/gen_driver_specs.py). Do not edit."""',
        "",
        "from __future__ import annotations",
        "",
        "from .base import Cap, CapDetail, EqInfo, ParametricEqInfo",
        "",
        "SPECS: dict[str, dict] = {",
    ]
    for d in devices:
        if d["name"] == "HeadsetControl Test device":
            continue
        caps = ", ".join(
            f"Cap.{_cap(c['cap'])}: CapDetail({c['usagepage']:#06x}, {c['usageid']:#06x}, {c['interface']})"
            for c in d["caps"]
        )
        eq = "None"
        dynamic = d["name"] in EQ_FROM_DEVICE
        if d["eq"] and not dynamic:
            e = d["eq"]
            eq = f"EqInfo({e['bands']}, {e['baseline']}, {float(e['step'])!r}, {e['min']}, {e['max']})"
        peq = "None"
        if d["peq"] and not dynamic:
            p = d["peq"]
            peq = (f"ParametricEqInfo({p['bands']}, {float(p['gain_base'])!r}, {float(p['gain_step'])!r}, "
                   f"{float(p['gain_min'])!r}, {float(p['gain_max'])!r}, {float(p['q_min'])!r}, "
                   f"{float(p['q_max'])!r}, {p['freq_min']}, {p['freq_max']}, {p['filter_types']})")
        presets = ", ".join(f"({p['name']!r}, {tuple(float(v) for v in p['values'])!r})" for p in d["presets"])
        preset_count = len({c["arg"] for c in d["calls"] if c["call"] == "eq_preset"})
        lines += [
            f"    {d['name']!r}: dict(",
            f"        vendor={d['vendor']:#06x},",
            f"        pids=({', '.join(f'{p:#06x}' for p in d['pids'])},),",
            f"        caps={{{caps}}},",
            f"        eq={eq},",
            f"        peq={peq},",
            f"        presets=({presets}{',' if len(d['presets']) == 1 else ''}),",
            f"        preset_count={preset_count},",
            "    ),",
        ]
    lines.append("}")
    OUT.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT} ({len(devices) - 1} devices)")


_CAP_NAMES = {
    "CAP_SIDETONE": "SIDETONE", "CAP_BATTERY_STATUS": "BATTERY", "CAP_NOTIFICATION_SOUND": "NOTIFICATION_SOUND",
    "CAP_LIGHTS": "LIGHTS", "CAP_INACTIVE_TIME": "INACTIVE_TIME", "CAP_CHATMIX_STATUS": "CHATMIX",
    "CAP_VOICE_PROMPTS": "VOICE_PROMPTS", "CAP_ROTATE_TO_MUTE": "ROTATE_TO_MUTE", "CAP_EQUALIZER_PRESET": "EQ_PRESET",
    "CAP_EQUALIZER": "EQUALIZER", "CAP_PARAMETRIC_EQUALIZER": "PARAMETRIC_EQ",
    "CAP_MICROPHONE_MUTE_LED_BRIGHTNESS": "MIC_MUTE_LED", "CAP_MICROPHONE_VOLUME": "MIC_VOLUME",
    "CAP_VOLUME_LIMITER": "VOLUME_LIMITER", "CAP_BT_WHEN_POWERED_ON": "BT_WHEN_POWERED_ON",
    "CAP_BT_CALL_VOLUME": "BT_CALL_VOLUME", "CAP_NOISE_FILTER": "NOISE_FILTER", "CAP_SIDETONE_STATUS": "SIDETONE_STATUS",
}


def _cap(name: str) -> str:
    return _CAP_NAMES[name]


if __name__ == "__main__":
    main()
