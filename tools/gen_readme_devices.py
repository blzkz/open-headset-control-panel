#!/usr/bin/env python3
"""Rewrite the HeadsetControl-port table in README.md (between the devices markers)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openlogi.drivers import REGISTRY  # noqa: E402
from openlogi.drivers.base import Cap  # noqa: E402
from openlogi.drivers.session import VENDOR_NAMES  # noqa: E402

START, END = "<!-- devices:start -->", "<!-- devices:end -->"
LABELS = {
    Cap.BATTERY: "batería", Cap.SIDETONE: "sidetone", Cap.EQUALIZER: "EQ", Cap.PARAMETRIC_EQ: "EQ paramétrico",
    Cap.EQ_PRESET: "presets", Cap.CHATMIX: "chat mix", Cap.INACTIVE_TIME: "apagado auto.", Cap.LIGHTS: "luces",
    Cap.VOICE_PROMPTS: "avisos de voz", Cap.ROTATE_TO_MUTE: "girar para silenciar", Cap.MIC_VOLUME: "vol. micro",
    Cap.MIC_MUTE_LED: "LED de silencio", Cap.VOLUME_LIMITER: "limitador", Cap.NOISE_FILTER: "filtro de ruido",
    Cap.BT_WHEN_POWERED_ON: "BT al encender", Cap.BT_CALL_VOLUME: "llamadas BT", Cap.NOTIFICATION_SOUND: "sonidos",
}


def table() -> str:
    rows = ["| Modelo | Marca | IDs USB | Funciones |", "|---|---|---|---|"]
    for cls in sorted(REGISTRY.drivers, key=lambda c: (VENDOR_NAMES.get(c.VENDOR, ""), c.NAME)):
        ids = ", ".join(f"`{cls.VENDOR:04x}:{p:04x}`" for p in cls.PIDS[:4]) + (" …" if len(cls.PIDS) > 4 else "")
        caps = ", ".join(label for cap, label in LABELS.items() if cap in cls.CAPS)
        rows.append(f"| {cls.NAME} | {VENDOR_NAMES.get(cls.VENDOR, f'{cls.VENDOR:04x}')} | {ids} | {caps} |")
    return "\n".join(rows)


def main() -> None:
    readme = ROOT / "README.md"
    text = readme.read_text()
    head, rest = text.split(START, 1)
    _old, tail = rest.split(END, 1)
    readme.write_text(f"{head}{START}\n{table()}\n{END}{tail}")
    print(f"updated {readme.relative_to(ROOT)} ({len(REGISTRY.drivers)} models)")


if __name__ == "__main__":
    main()
