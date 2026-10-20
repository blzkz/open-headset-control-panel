#!/usr/bin/env python3
"""Generate tests/data/golden_scripts.txt: scripted device replies for the golden harness.

The canned patterns (zeros/ramp/ff/echo) never form a valid reply for protocols with
framing, checksums or multi-step handshakes, so those drivers were only checked up to
their first packet. A script answers each request like a real device would, letting both
the C++ reference and the Python port walk the full protocol (lookups, fragmentation,
EQ encoding, error paths). Variants run in file order on the same driver instance, so
the first one sees the uncached state.

Format, one rule per line, first match wins:
    [Device name] variant
    <op> <index>=<hex byte> ... -> <reply hex> <reply hex> ... [| copy=A-B sum=A-B@C]
A write matching no rule keeps the pending replies (multi-report messages are answered
after their last fragment). Modifiers apply to every reply of the rule, in order:
``copy=A-B`` copies bytes A..B of the written report (sequence numbers, transaction ids),
``sum=A-B@C`` stores the low byte of the sum of reply bytes A..B at C (checksums).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "tests" / "data" / "golden_scripts.txt"
lines: list[str] = []


def section(device: str, variant: str, comment: str = "") -> None:
    lines.append("")
    if comment:
        lines.append(f"# {comment}")
    lines.append(f"[{device}] {variant}")


def rule(match: list[tuple[int, int]], *replies: bytes, op: str = "write", note: str = "",
         mods: str = "") -> None:
    spec = " ".join(f"{i}={v:02x}" for i, v in match)
    tail = " ".join(r.hex() for r in replies)
    line = f"{op} {spec} -> {tail}".rstrip() + (f" | {mods}" if mods else "")
    lines.append(line + (f"  # {note}" if note else ""))


def at(start: int, data) -> list[tuple[int, int]]:
    return [(start + i, b) for i, b in enumerate(data)]


# -- Logitech Centurion ----------------------------------------------------------------------

BRIDGE = 0x07
SUB_FEATURES = {0x0001: 0x01, 0x0104: 0x02, 0x0108: 0x03, 0x020D: 0x04, 0x0601: 0x05, 0x060B: 0x06,
                0x0621: 0x08, 0x0636: 0x09, 0x0604: 0x0A}


class Centurion:
    def __init__(self, prefix: tuple[int, ...]):
        self.p = prefix

    def frame(self, payload, flags: int = 0) -> bytes:
        return bytes(self.p) + bytes((len(payload) + 1, flags)) + bytes(payload)

    def direct(self, fi: int, fn: int, params, *replies: bytes, note: str = "") -> None:
        rule(at(0, self.frame((fi, (fn & 0xF0) | 1, *params))), *replies, note=note)

    def direct_reply(self, fi: int, fn: int, data) -> bytes:
        return self.frame((fi, (fn & 0xF0) | 1, *data))

    def bridge(self, sub: int, fn: int, params=None, *replies: bytes, flags: int = 0, note: str = "") -> None:
        n = len(self.p)
        head = [(n + 1, flags), (n + 2, BRIDGE), (n + 3, 0x11), (n + 6, 0x00), (n + 7, sub), (n + 8, (fn & 0xF0) | 1)]
        if params is not None:
            head += at(n + 9, params)
        rule(at(0, self.p) + head, *replies, note=note)

    def ack(self) -> bytes:
        return self.frame((BRIDGE, 0x11))

    def resp(self, sub: int, fn: int, data=()) -> bytes:
        return self.frame((BRIDGE, 0x10, 0x00, 0x00, 0x00, sub, (fn & 0xF0) | 1, *data))

    def ok(self, sub: int, fn: int, data=(), params=None, note: str = "") -> None:
        self.bridge(sub, fn, params, self.ack(), self.resp(sub, fn, data), note=note)

    def lookups(self, missing: tuple[int, ...] = ()) -> None:
        self.direct(0x00, 0x00, (0x00, 0x03), self.direct_reply(0x00, 0x00, (BRIDGE,)), note="bridge lookup")
        for fid, index in SUB_FEATURES.items():
            self.ok(0x00, 0x00, (0 if fid in missing else index, 0x00, 0x01), params=fid.to_bytes(2, "big"),
                    note=f"feature 0x{fid:04x}")


def g522() -> None:
    name = "Logitech G522 LIGHTSPEED"
    c = Centurion((0x50, 0x23))
    section(name, "edge", "error paths: range checks, probe after a lost reply, connection events, rejects")
    c.lookups()
    c.ok(0x02, 0x00, (150, 0, 0), note="battery 150% -> protocol error")
    c.bridge(0x0A, 0x10, None, c.ack(), note="sidetone set: ack, then nothing -> offline probe")
    c.direct(BRIDGE, 0x00, (), c.direct_reply(BRIDGE, 0x00, (0, 0, 0, 0)), note="bridge status: disconnected")
    c.ok(0x0A, 0x00, (0x01, 0xFF, 0x00, 0x0C), note="sidetone 12 > max 9")
    c.bridge(0x08, 0x30, None, c.ack(), c.frame((BRIDGE, 0x00, 0x00, 0x00)), note="lights: disconnect event")
    c.ok(0x03, 0x00, (0x1E, 0xAA), note="auto-sleep reply too short")
    c.bridge(0x06, 0x50, None, c.frame((0xFF, BRIDGE, 0x11, 0x00)), note="voice prompts: bridge rejects")
    c.bridge(0x04, 0x20, None, c.ack(), c.frame((BRIDGE, 0x10, 0, 0, 0x00, 0xFF, 0x04, 0x00)),
             note="EQ: sub-device error")
    c.ok(0x05, 0x20)
    section(name, "main", "a connected headset")
    c.lookups()
    c.ok(0x02, 0x00, (87, 0, 1))
    c.ok(0x0A, 0x10)
    c.ok(0x0A, 0x00, (0x01, 0xFF, 0x00, 0x05))
    c.ok(0x08, 0x30)
    c.ok(0x06, 0x50)
    c.ok(0x03, 0x00, (0x1E, 0xAA, 0xBB))
    c.ok(0x03, 0x10)
    c.ok(0x04, 0x20)
    c.ok(0x05, 0x20)


def pro_x2() -> None:
    name = "Logitech G PRO X 2 LIGHTSPEED"
    c = Centurion((0x51,))
    section(name, "legacy", "older firmware: no battery SoC (legacy battery), no advanced EQ (onboard EQ)")
    c.lookups(missing=(0x0104,))
    rule(at(0, (0x51, 0x08, 0x00, 0x03, 0x1A, 0x00, 0x03, 0x00, 0x04, 0x0A)), bytes((0x51, 0x03)),
         bytes((0x51, 0x05, 0, 0, 0, 0, 0x01)), bytes((0x51, 0x0B, 0, 0, 0, 0, 0, 0, 0x04, 0, 75, 0, 0x02)),
         note="legacy battery: ack, power event, reply")
    c.ok(0x0A, 0x00, (0x01, 0x32), note="sidetone v1 state")
    c.ok(0x0A, 0x10)
    c.ok(0x03, 0x10)
    c.ok(0x04, 0x00, (0x00, 0x00, 0x00), note="advanced EQ info too short -> onboard EQ")
    c.ok(0x09, 0x00, (0, 0, 0, 0, 0))
    bands = ((100, 0, 1), (500, 0, 1), (2000, 0, 1), (4000, 0, 1), (10000, 0, 1))
    c.ok(0x09, 0x10, (0x00, len(bands), *[x for f, g, q in bands for x in (f >> 8, f & 0xFF, g, q)]),
         params=(0x00,))
    c.bridge(0x09, 0x20, None, c.ack(), c.resp(0x09, 0x20), flags=0x01, note="onboard EQ write (fragmented)")
    section(name, "main", "current firmware")
    c.lookups()
    c.ok(0x02, 0x00, (57, 0, 2))
    c.ok(0x0A, 0x00, (0x01, 0xFF, 0x00, 0x32), note="sidetone v2 state")
    c.ok(0x0A, 0x10)
    c.ok(0x03, 0x10)
    c.ok(0x04, 0x00, (0x00, 0x00, 0x00, 0xF4, 0x0C), note="advanced EQ: -12..12 dB")
    c.ok(0x04, 0x30, (0x02,), note="active slot")
    c.ok(0x04, 0x10, (0x00, 0x64, 0, 0x01, 0xF4, 0, 0x07, 0xD0, 0, 0x0F, 0xA0, 0, 0x27, 0x10, 0, 0, 0, 0))
    c.ok(0x04, 0x20)


# -- ASTRO A50 Gen 4 -------------------------------------------------------------------------


def a50_gen4() -> None:
    name = "Logitech ASTRO A50 Gen 4"

    def req(cmd: int, *replies: bytes, arg: int | None = None, note: str = "") -> None:
        rule([(0, 0x02), (1, cmd)] + ([(3, arg)] if arg is not None else []), *replies, note=note)

    def ok(*payload: int) -> bytes:
        return bytes((0x02, 0x02, 0x00, *payload))

    section(name, "edge", "unlinked headset, refusals, a lost reply (pending recovery)")
    req(0x54, ok(0x00), note="not linked")
    req(0x68, bytes((0x02, 0x00)), note="accepted without data")
    req(0x62, bytes((0x02, 0x01, 0x00, 0x07, 0, 0, 0, 0x45, 0x52, 0x52)), note="error status")
    req(0x64, bytes((0x02, 0x00)))
    req(0x67, note="no reply")
    req(0x6C, ok(0x07), note="active preset out of range")
    section(name, "main", "linked headset")
    req(0x54, ok(0x02))
    req(0x7C, ok(0xD5))
    req(0x72, ok(0x40))
    req(0x62, ok(0x62, 0x05), arg=0x05)
    req(0x62, ok(0x62, 0x04), arg=0x04)
    req(0x68, ok(0x68, 0x05, 0x32))
    req(0x64, ok(0x00), arg=0x00)
    req(0x64, ok(0x01), arg=0x01)
    req(0x64, ok(0x02), arg=0x03, note="echo mismatch")
    for p in (1, 2, 3):
        req(0x67, ok(0x67, p), arg=p)
    req(0x6C, ok(0x02), note="active preset 2")
    req(0x63, ok(0x63, 0x02))
    req(0x6F, ok(0x6F))


# -- Audeze Maxwell --------------------------------------------------------------------------


def _maxwell_reports(message: bytes) -> list[bytes]:
    return [bytes((0x07, len(message[o:o + 59]), 0x80)) + message[o:o + 59] for o in range(0, len(message), 59)]


def _msg(typ: int, body) -> bytes:
    body = bytes(body)
    return bytes((0x05, typ, len(body) & 0xFF, len(body) >> 8)) + body


def maxwell(name: str, eq: bool) -> None:
    from openlogi.drivers.audeze import EQ_FREQUENCIES, eq_coefficients

    def req(cmd: tuple[int, int], *messages: bytes, arg=(), note: str = "") -> None:
        reports = [r for m in messages for r in _maxwell_reports(m)]
        rule([(0, 0x06), (2, 0x80), (3, 0x05), (4, 0x5A), (7, cmd[0]), (8, cmd[1])] + at(9, arg), *reports, note=note)

    def read_key(key: int, value: int, status: int = 0) -> None:
        req((0x01, 0x09), _msg(0x5B, (0x01, 0x09, key, 0x00, status, value)), arg=(key,))

    def write_key(key: int) -> None:
        req((0x00, 0x09), _msg(0x5B, (0x00, 0x09, key, 0x00)), arg=(key,))

    def read_param(param: int, value: int) -> None:
        req((0x83, 0x2C), _msg(0x5B, (0x83, 0x2C, 0x00, param, 0x00, value)), arg=(param,))

    def write_param(param: int, status: int = 0) -> None:
        req((0x82, 0x2C), _msg(0x5B, (0x82, 0x2C, status, param, 0x00)), arg=(param,))

    section(name, "edge", "refusals, short replies and silence")
    req((0xD6, 0x0C), _msg(0x5B, (0xD6, 0x0C, 0x01)), note="battery refused")
    read_key(0x2D, 0, status=1)
    read_key(0x2C, 0, status=1)
    read_key(0x00, 2)  # not a custom preset: EQ edits refused
    req((0x83, 0x2C), _msg(0x5B, (0x83, 0x2C, 0x00, 0x0B, 0x00)), arg=(0x0B,), note="chatmix reply too short")
    read_param(0x07, 0)
    write_param(0x07)
    write_param(0x01, status=1)
    for key in (0x00, 0x24, 0x28):
        write_key(key)

    section(name, "main", "a connected headset with custom preset slot 1 selected")
    req((0xD6, 0x0C), _msg(0x5D, (0xD6, 0x0C, 0x00, 0x00, 75)))
    read_key(0x22, 0)
    read_key(0x2D, 0x14)
    read_key(0x2C, 0x0A)
    read_param(0x07, 1)
    write_param(0x07)
    write_param(0x01)
    read_param(0x0B, 15)
    for key in (0x00, 0x20, 0x24, 0x28, 0x2C):
        write_key(key)
    if eq:
        read_key(0x00, 8)
        blob = bytearray(5)
        for f in EQ_FREQUENCIES:
            rec = bytearray(18)
            rec[2:6] = (f * 100).to_bytes(4, "little")
            rec[10:14] = round(f * 70).to_bytes(4, "little")
            blob += rec
        coeffs = eq_coefficients([(float(f), 0.0, round(f * 70) * 0.01) for f in EQ_FREQUENCIES])

        def stored(key: int, data: bytes) -> bytes:
            return _msg(0x5B, (0x0C, 0x0A, 0x00, key & 0xFF, key >> 8, len(data) & 0xFF, len(data) >> 8, *data))

        req((0x0C, 0x0A), stored(0xEF01, blob), arg=(0x01, 0xEF), note="custom EQ settings, flat")
        req((0x0C, 0x0A), stored(0xE42D, coeffs), arg=(0x2D, 0xE4), note="coefficients of the flat EQ")
        req((0x03, 0x0E), _msg(0x5B, (0x03, 0x0E, 0x00)), note="apply EQ")
        req((0x03, 0x0A), _msg(0x5B, (0x03, 0x0A, 0x00)), note="reclaim storage")
        req((0x0D, 0x0A), _msg(0x5B, (0x0D, 0x0A, 0x00, 0x01, 0xEF)), arg=(0x01, 0xEF))
        req((0x0D, 0x0A), _msg(0x5B, (0x0D, 0x0A, 0x00, 0x2D, 0xE4)), arg=(0x2D, 0xE4))


# -- Lenovo / Plantronics / Jabra / Sony ------------------------------------------------------


def lenovo() -> None:
    name = "Lenovo Wireless VoIP Headset"

    def req(cmd: int, *payload: int, status: int = 0, note: str = "") -> None:
        reply = bytes((0x27, cmd, status, 0x00, *payload))
        rule([(0, 0x24), (1, cmd)], reply, op="feature_set", note=note)

    section(name, "edge", "headset not connected to the dongle; a reply for another command")
    req(0x01, status=0x01, note="offline")
    rule([(0, 0x24), (1, 0x0E)], bytes((0x27, 0x0F, 0x00)), op="feature_set", note="wrong command")
    req(0x08, 0x00)
    section(name, "main", "connected")
    req(0x01, 0x00, 0x00, 0x00, 75)
    req(0x0E, 0x03)
    req(0x08, 0x02, note="two hours")
    for cmd in (0x05, 0x04, 0x02, 0x15, 0x07):
        req(cmd, 0x01)


def plantronics() -> None:
    name = "Plantronics Voyager 8200 UC (BT600)"
    head = (0x07, 0x01, 0x01, 0x10)

    def frame(addr: int, op: int, grp: int, item: int, *value: int) -> bytes:
        return bytes((*head, 0x07, addr, 0x00, 0x00, op, grp, item, *value))

    section(name, "edge", "headset out of range: the dongle answers, the headset does not")
    rule([(0, 0x07), (5, 0x00), (8, 0x01)], frame(0x00, 0x08, 0x01, 0x02), note="dongle hello")
    rule([(0, 0x07), (5, 0x20), (8, 0x01)], note="headset hello: silence")
    section(name, "main", "connected")
    rule([(0, 0x07), (5, 0x00), (8, 0x01)], frame(0x00, 0x08, 0x01, 0x02))
    rule([(0, 0x07), (5, 0x20), (8, 0x01)], bytes((0x07, 0x05)), frame(0x02, 0x08, 0x01, 0x02),
         note="an unrelated event first")
    rule([(0, 0x07), (8, 0x02), (9, 0x0A), (10, 0x1A)], frame(0x02, 0x03, 0x0A, 0x1A, 1, 5, 1, 0x01, 0x2C),
         note="battery 1 of 4 steps, charging")
    for grp, item in ((0x04, 0x10), (0x04, 0x09), (0x04, 0x11), (0x0F, 0x0E)):
        rule([(0, 0x07), (8, 0x05), (9, grp), (10, item)], frame(0x02, 0x06, grp, item))


def jabra(name: str, address: int) -> None:
    def frame(typ: int, cmd: int, sub: int, *payload: int) -> bytes:
        return bytes((0x05, 0x00, address, 0x00, (typ << 6) | (6 + len(payload)), cmd, sub, *payload))

    def get(cmd: int, sub: int, *replies: bytes, note: str = "") -> None:
        rule([(0, 0x05), (4, 0x46), (5, cmd), (6, sub)], *replies, note=note, mods="copy=3-3")

    def set_(cmd: int, sub: int, *replies: bytes, note: str = "") -> None:
        rule([(0, 0x05), (4, 0x80 | 0x06 + 1), (5, cmd), (6, sub)], *replies, note=note, mods="copy=3-3")
        rule([(0, 0x05), (4, 0x80 | 0x06 + 2), (5, cmd), (6, sub)], *replies, note=note, mods="copy=3-3")

    nack = lambda reason: frame(3, 0xFE, reason)  # noqa: E731
    section(name, "edge", "rejections (NACK reasons) and a headset out of range")
    get(0x12, 0x02, nack(0xF3), note="peer offline")
    get(0x13, 0x7C, frame(3, 0x13, 0x7C, 0x00, 0x05), note="sidetone value not in the table")
    set_(0x12, 0x08, nack(0xF6), note="not supported")
    set_(0x13, 0x3A, nack(0x01), note="other NACK")
    set_(0x13, 0x90, frame(3, 0x13, 0x90), note="not an ACK")
    set_(0x13, 0x01, nack(0xFF))
    section(name, "main", "connected")
    get(0x12, 0x02, frame(3, 0x12, 0x02, 0x01, 87), note="charging, 87 %")
    get(0x13, 0x7C, frame(3, 0x13, 0x7C, 0x00, 0xFD), note="sidetone -3 dB, unmuted")
    for cmd, sub in ((0x13, 0x7C), (0x12, 0x08), (0x13, 0x3A), (0x13, 0x90), (0x13, 0x01)):
        set_(cmd, sub, frame(3, 0xFF, sub))


def sony_h5() -> None:
    name = "Sony INZONE H5"

    def event(eid: int, typ: int, *payload: int, tid_copy: bool = True) -> tuple[bytes, str]:
        n = 12 + len(payload)
        reply = bytes((0x02, n, 0x04, 0xFF, 0x00, 0x00, 0x96, 0xC3, 0x14, eid, typ, 0x00, 0x00, *payload))
        return reply, f"copy=11-12 sum=5-{n}@{n + 1}" if tid_copy else f"sum=5-{n}@{n + 1}"

    def req(eid: int, typ: int, *payload: int, junk: bool = False, note: str = "") -> None:
        reply, mods = event(eid, typ, *payload)
        replies = [reply]
        if junk:  # an unrelated notification before the answer
            replies.insert(0, event(0x01, 0x20, 0x01)[0])
        rule([(0, 0x02), (2, 0x01), (9, eid)], *replies, note=note, mods=mods)

    section(name, "edge", "headset off, out-of-range values")
    req(0x04, 0x10, 0x00, 0xFF, note="battery 0xff: offline")
    req(0x22, 0x10, 0x5B, note="balance above 90")
    req(0x23, 0x10, note="answer of the wrong type for a set")
    section(name, "main", "connected")
    req(0x04, 0x10, 0x01, 0x50, junk=True, note="charging, 80 %")
    req(0x22, 0x10, 0x2D, note="balance 45 of 90")
    req(0x23, 0x20)
    req(0x24, 0xA0)


def main() -> None:
    lines.append("# Generated by tools/golden/make_scripts.py; do not edit. Format: see that script.")
    g522()
    pro_x2()
    a50_gen4()
    maxwell("Audeze Maxwell", eq=True)
    maxwell("Audeze Maxwell 2", eq=False)
    lenovo()
    plantronics()
    for jabra_name, address in (("Jabra Link 380 (paired headset)", 0x04), ("Jabra Link 390 (paired headset)", 0x04),
                                ("Jabra Evolve2 65 Flex (USB)", 0x08)):
        jabra(jabra_name, address)
    sony_h5()
    OUT.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
