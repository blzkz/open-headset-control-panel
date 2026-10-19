"""Command-line interface (scriptable, HeadsetControl-like)."""

from __future__ import annotations

import argparse
import json
import sys

from .battery import BatteryEstimator
from .device import Headset, charge_cable_connected, find_receivers
from .hidpp import HidppError, HidppTimeout, TransportError
from .hidpp.protocol import SOFTWARE_ID_CLI
from .presets import BUILTIN_PRESETS, PRESETS_BY_KEY


def _parse_gains(text: str) -> list[int]:
    try:
        return [int(round(float(x))) for x in text.replace(";", ",").split(",") if x.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma separated dB values") from exc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="openlogi-headsetctl",
        description="Control Logitech PRO X Wireless (LIGHTSPEED) headsets over HID++ 2.0",
    )
    p.add_argument("-b", "--battery", action="store_true", help="print battery level")
    p.add_argument("-s", "--sidetone", type=int, metavar="0-100", help="set sidetone level")
    p.add_argument("-e", "--equalizer", type=_parse_gains, metavar="G1,...,G10",
                   help="set 10-band EQ gains in dB (-12..12)")
    p.add_argument("-p", "--eq-preset", choices=[x.key for x in BUILTIN_PRESETS], help="apply an EQ preset")
    p.add_argument("-i", "--info", action="store_true", help="print device information and current state")
    p.add_argument("--list-presets", action="store_true", help="list built-in EQ presets")
    p.add_argument("--json", action="store_true", help="machine readable output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_presets:
        for pr in BUILTIN_PRESETS:
            print(f"{pr.key:12s} {pr.name:18s} {','.join(map(str, pr.gains))}")
        return 0
    if not any((args.battery, args.sidetone is not None, args.equalizer, args.eq_preset, args.info)):
        args.info = True

    receivers = find_receivers()
    if not receivers:
        print("No supported Logitech headset receiver found.", file=sys.stderr)
        return 2
    try:
        hs = Headset.open(receivers[0], sw_id=SOFTWARE_ID_CLI)
    except TransportError as exc:
        print(f"Cannot open {receivers[0].path}: {exc}. Install the udev rule (see README).", file=sys.stderr)
        return 3

    out: dict[str, object] = {}
    try:
        hs.probe()
        if args.sidetone is not None:
            out["sidetone"] = hs.sidetone.set(args.sidetone)
        gains = list(PRESETS_BY_KEY[args.eq_preset].gains) if args.eq_preset else args.equalizer
        if gains:
            out["equalizer"] = hs.equalizer.set(gains)
        if args.battery or args.info:
            r = hs.read_battery()
            if r is not None:
                b = BatteryEstimator().update(r.millivolts, r.charging, r.charge_complete)
                out["battery"] = {"percent": b.percent, "millivolts": b.millivolts,
                                  "charging": b.charging, "full": b.full}
        if args.info:
            ident = hs.identity
            out.update(
                name=ident.name,
                hidraw=hs.info.path,
                usb_id=hs.info.usb_id,
                hidpp=f"{ident.protocol[0]}.{ident.protocol[1]}",
                firmware=[fw.version for fw in ident.firmware],
                sidetone=hs.sidetone.get() if hs.caps.sidetone else None,
                equalizer=hs.equalizer.get() if hs.caps.equalizer else None,
                eq_bands_hz=list(hs.eq_info.frequencies) if hs.eq_info else None,
                charge_cable=charge_cable_connected(hs.spec),
            )
    except HidppTimeout:
        print("Headset is switched off or out of range.", file=sys.stderr)
        return 4
    except (HidppError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        hs.close()

    if args.json:
        print(json.dumps(out, indent=2))
    elif args.battery and len(out) == 1:
        b = out["battery"]
        print(f"{b['percent']}%{' (charging)' if b['charging'] else ''}")
    else:
        for key, value in out.items():
            if isinstance(value, dict):
                value = ", ".join(f"{k}={v}" for k, v in value.items())
            elif isinstance(value, list):
                value = " ".join(map(str, value))
            print(f"{key:14s} {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
