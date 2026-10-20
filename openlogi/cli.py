"""Command-line interface (scriptable, HeadsetControl-like)."""

from __future__ import annotations

import argparse
import json
import sys

from .battery import BatteryEstimator
from .device import LOGITECH_VID, Headset, charge_cable_connected, find_receivers
from .hidpp import FeatureId, HidppError, HidppTimeout, TransportError, enumerate_hidraw
from .hidpp.features import LevelReading, LightMode
from .models import BY_PID, MODELS
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
        description="Control Logitech HID++ 2.0 gaming headsets (PRO X Wireless, G733, G935, G535, …)",
    )
    p.add_argument("-b", "--battery", action="store_true", help="print battery level")
    p.add_argument("-s", "--sidetone", type=int, metavar="0-100", help="set sidetone level")
    p.add_argument("-e", "--equalizer", type=_parse_gains, metavar="G1,...,G10",
                   help="set 10-band EQ gains in dB (-12..12)")
    p.add_argument("-p", "--eq-preset", choices=[x.key for x in BUILTIN_PRESETS], help="apply an EQ preset")
    p.add_argument("-i", "--info", action="store_true", help="print device information and current state")
    p.add_argument("-a", "--auto-off", type=int, metavar="MIN", help="power off after MIN idle minutes (0 = never)")
    p.add_argument("-l", "--lights", choices=["off", "static", "breathing"], help="lighting effect (RGB models)")
    p.add_argument("--color", default="00b4f5", metavar="RRGGBB", help="lighting colour (default 00b4f5)")
    p.add_argument("--brightness", type=int, default=100, metavar="1-100", help="lighting brightness")
    p.add_argument("--list-devices", action="store_true", help="list supported models")
    p.add_argument("--diagnose", action="store_true",
                   help="read-only report of every HID++ feature, to attach to bug reports")
    p.add_argument("--list-presets", action="store_true", help="list built-in EQ presets")
    p.add_argument("--json", action="store_true", help="machine readable output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_presets:
        for pr in BUILTIN_PRESETS:
            print(f"{pr.key:12s} {pr.name:18s} {','.join(map(str, pr.gains))}")
        return 0
    if args.list_devices:
        for m in MODELS:
            pids = " ".join(f"046d:{p:04x}" for p in m.pids)
            print(f"{m.name:30s} {pids:34s} {'verified' if m.verified else 'experimental'}")
        return 0
    if args.diagnose:
        return diagnose()
    actions = (args.battery, args.sidetone is not None, args.equalizer, args.eq_preset, args.auto_off is not None,
               args.lights)
    if not any(actions):
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
        if args.auto_off is not None:
            if not hs.caps.auto_off:
                raise ValueError("this headset has no power-off timer")
            out["auto_off"] = hs.auto_off.set(args.auto_off)
        if args.lights:
            if not hs.caps.lights:
                raise ValueError("this headset has no controllable lighting")
            rgb = tuple(bytes.fromhex(args.color.lstrip("#"))[:3])
            mode = {"off": LightMode.OFF, "static": LightMode.STATIC, "breathing": LightMode.BREATHING}[args.lights]
            for zone, _label in hs.spec.light_zones:
                hs.leds.set_zone(zone, mode, rgb, args.brightness)
            out["lights"] = args.lights
        if args.battery or args.info:
            r = hs.read_battery()
            if isinstance(r, LevelReading):
                out["battery"] = {"percent": r.percent, "charging": r.charging, "full": r.full}
            elif r is not None:
                b = BatteryEstimator(curve=hs.spec.curve).update(r.millivolts, r.charging, r.charge_complete)
                out["battery"] = {"percent": b.percent, "millivolts": b.millivolts,
                                  "charging": b.charging, "full": b.full}
        if args.info:
            ident = hs.identity
            out.update(
                model=hs.spec.name + ("" if hs.spec.verified else " (experimental)"),
                name=ident.name,
                hidraw=hs.info.path,
                usb_id=hs.info.usb_id,
                hidpp=f"{ident.protocol[0]}.{ident.protocol[1]}",
                firmware=[fw.version for fw in ident.firmware],
                sidetone=hs.sidetone.get() if hs.caps.sidetone else None,
                equalizer=hs.equalizer.get() if hs.caps.equalizer else None,
                eq_bands_hz=list(hs.eq_info.frequencies) if hs.eq_info else None,
                auto_off=hs.auto_off.get() if hs.caps.auto_off else None,
                lights=[label for _, label in hs.spec.light_zones] if hs.caps.lights else None,
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


def diagnose() -> int:
    """Read-only dump for bug reports: hidraw nodes, HID++ features, getter replies."""
    from . import __version__

    report: dict[str, object] = {"openlogi": __version__, "nodes": [], "devices": []}
    for info in enumerate_hidraw(LOGITECH_VID):
        report["nodes"].append({  # type: ignore[union-attr]
            "path": info.path, "usb_id": info.usb_id, "name": info.name, "phys": info.phys,
            "hidpp_long": info.hidpp, "vendor_page": info.vendor_page,
            "model": BY_PID[info.product_id].name if info.product_id in BY_PID else None,
        })
    for info in find_receivers():
        entry: dict[str, object] = {"path": info.path, "usb_id": info.usb_id}
        try:
            hs = Headset.open(info, sw_id=SOFTWARE_ID_CLI)
        except TransportError as exc:
            entry["error"] = f"open: {exc}"
            report["devices"].append(entry)  # type: ignore[union-attr]
            continue
        try:
            entry["protocol"] = ".".join(map(str, hs.dev.ping()))
            feats = []
            for idx, fid, ftype, ver in hs.dev.enumerate_features():
                row: dict[str, object] = {"index": idx, "id": f"0x{fid:04X}", "name": FeatureId.label(fid),
                                          "type": ftype, "version": ver}
                # function 0 of these features is a pure getter on every known device
                if fid in (0x0003, 0x0005, 0x1000, 0x1004, 0x1F20, 0x8300, 0x8310, 0x8070):
                    try:
                        row["fn0"] = hs.dev.request(idx, 0).hex(" ")
                    except HidppError as exc:
                        row["fn0"] = f"error: {exc}"
                feats.append(row)
            entry["features"] = feats
            hs.probe()
            entry["name"] = hs.identity.name
            entry["firmware"] = [fw.version for fw in hs.identity.firmware]
        except HidppTimeout:
            entry["error"] = "headset off or out of range"
        except HidppError as exc:
            entry["error"] = str(exc)
        finally:
            hs.close()
        report["devices"].append(entry)  # type: ignore[union-attr]
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
