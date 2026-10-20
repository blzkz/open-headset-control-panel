"""Supported Logitech HID++ 2.0 headsets.

Capabilities are *not* listed here: they are discovered from each headset's
HID++ feature table at runtime, so a model only needs an entry to be recognised
(name, picture, battery curve and lighting zones). ``verified`` marks models
tested on real hardware; the rest come from community reverse-engineering
(HeadsetControl, g933-utils) and are shown as experimental.
"""

from __future__ import annotations

from dataclasses import dataclass

# (millivolts, percent), ascending voltage.
Curve = tuple[tuple[int, float], ...]

CURVE_PRO_X: Curve = (  # densified G PRO calibration, verified on a PRO X Wireless
    (3320, 0.0), (3550, 2.0), (3670, 5.0), (3710, 12.0), (3740, 20.0), (3780, 30.0), (3805, 40.0),
    (3830, 50.0), (3880, 60.0), (3940, 70.0), (4000, 80.0), (4075, 90.0), (4150, 100.0),
)
CURVE_G533: Curve = ((3330, 0.0), (3680, 5.0), (3750, 20.0), (3790, 30.0), (3850, 50.0), (4200, 100.0))
CURVE_G535: Curve = ((3310, 0.0), (3664, 5.0), (3730, 20.0), (3766, 30.0), (3817, 50.0), (4175, 100.0))
CURVE_G633: Curve = ((3150, 0.0), (3300, 5.0), (3500, 10.0), (3650, 20.0), (3750, 40.0), (3850, 60.0),
                     (3950, 80.0), (4100, 100.0))

# Lighting zones of the 0x8070 colour-LED feature (zone index, label).
ZONES_G933: tuple[tuple[int, str], ...] = ((0, "Logo"), (1, "Strips"))


@dataclass(frozen=True, slots=True)
class ModelSpec:
    key: str
    name: str
    pids: tuple[int, ...]
    image: str
    curve: Curve = CURVE_PRO_X
    wireless: bool = True
    verified: bool = False
    light_zones: tuple[tuple[int, str], ...] = ()
    # Product id that appears when the headset itself is plugged in for charging.
    charger_pid: int | None = None


MODELS: tuple[ModelSpec, ...] = (
    ModelSpec("pro-x-wireless", "PRO X Wireless", (0x0ABA, 0x0AAA), "headset-34-left.png",
              verified=True, charger_pid=0x0ABB),
    ModelSpec("pro-wireless", "PRO Wireless", (0x0AA7,), "headset-34-left.png"),
    ModelSpec("pro-x-2", "PRO X 2 LIGHTSPEED (HID++)", (0x0AFB, 0x0AFC), "headset-pro-x-2.png"),
    ModelSpec("g933-family", "G633 / G635 / G933 / G935", (0x0A5C, 0x0A89, 0x0A5B, 0x0A87),
              "headset-g935.png", curve=CURVE_G633, light_zones=ZONES_G933),
    ModelSpec("g733", "G733 LIGHTSPEED", (0x0AB5, 0x0AFE, 0x0B1F), "headset-g733.png",
              curve=CURVE_G633, light_zones=ZONES_G933),
    ModelSpec("g533", "G533 Wireless", (0x0A66,), "headset-g533.png", curve=CURVE_G533),
    ModelSpec("g535", "G535 LIGHTSPEED", (0x0AC4,), "headset-g535.png", curve=CURVE_G535),
    ModelSpec("g43x", "G431 / G432 / G433", (0x0A9B, 0x0A9C, 0x0A6D), "headset-g432.png", wireless=False),
)

BY_PID: dict[int, ModelSpec] = {pid: m for m in MODELS for pid in m.pids}
BY_KEY: dict[str, ModelSpec] = {m.key: m for m in MODELS}
ALL_PIDS: tuple[int, ...] = tuple(sorted({*BY_PID, *(m.charger_pid for m in MODELS if m.charger_pid)}))
