"""Byte-exact check of the ported drivers against HeadsetControl.

tests/data/headsetcontrol_golden.json.gz was produced by compiling HeadsetControl's
own driver classes against a recording HID interface (see tools/golden/). For every
capability call it stores the packets written and how canned replies were parsed.
Each Python port must reproduce those exactly.
"""

from __future__ import annotations

import gzip
import json
import unittest
from collections import OrderedDict
from pathlib import Path

from openlogi.drivers import REGISTRY
from openlogi.drivers.base import (
    Battery,
    Chatmix,
    DriverError,
    ErrorCode,
    FilterType,
    ParametricBand,
    Sidetone,
    set_clock,
    set_sleeper,
)

GOLDEN = Path(__file__).with_name("data") / "headsetcontrol_golden.json.gz"
SCRIPTS = Path(__file__).with_name("data") / "golden_scripts.txt"
PATTERNS = ("zeros", "ramp", "ff", "echo")
MAX_OPS = 48


def load_scripts(path: Path = SCRIPTS) -> dict[str, list[tuple[str, list]]]:
    """Scripted replies shared with the C++ harness: {device: [(variant, rules)]}.

    A rule is (op, [(index, byte)], [reply bytes]); see tools/golden/README.md.
    """
    scripts: dict[str, list[tuple[str, list]]] = {}
    rules: list = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            device, variant = line[1:].split("]", 1)
            rules = []
            scripts.setdefault(device, []).append((variant.strip(), rules))
            continue
        head, _, tail = line.partition("->")
        op, *matches = head.split()
        match = [(int(i), int(v, 16)) for i, v in (m.split("=") for m in matches)]
        rules.append((op, match, [bytes.fromhex(t.replace("_", "")) for t in tail.split()]))
    return scripts


class Recorder:
    """Python twin of the C++ recording interface used to build the golden file."""

    def __init__(self, pattern: str, rules: list | None = None):
        self.pattern = pattern
        self.rules = rules
        self.queue: list[bytes] = []
        self.ops: list[dict] = []
        self.dropped = 0
        self.last = b""
        self.idle_inputs = 0

    def _react(self, op: str, data: bytes) -> None:
        if self.rules is None:
            return
        for rop, match, replies in self.rules:
            if rop == op and all(i < len(data) and data[i] == v for i, v in match):
                self.queue = list(replies)
                return

    def _pop(self, n: int) -> bytes:
        return self.queue.pop(0)[:n].ljust(n, b"\0")

    def _push(self, op: dict) -> None:
        if len(self.ops) < MAX_OPS:
            self.ops.append(op)
        else:
            self.dropped += 1

    def _rec(self, op: str, data: bytes) -> None:
        self.idle_inputs = 0
        self._push({"op": op, "data": data.hex()})
        self.last = bytes(data)
        self._react(op, self.last)

    def _fill(self, n: int) -> bytearray:
        out = bytearray(n)
        for i in range(n):
            if self.pattern == "ramp":
                out[i] = i & 0xFF
            elif self.pattern == "ff":
                out[i] = 0xFF
            elif self.pattern == "echo":
                out[i] = self.last[i] if i < len(self.last) else (0x40 + i) & 0xFF
        return out

    def write(self, data: bytes, size: int | None = None) -> None:
        data = bytes(data)
        if size is not None:
            data = data[:size].ljust(size, b"\0")
        self._rec("write", data)

    def send_feature(self, data: bytes, size: int | None = None) -> None:
        data = bytes(data)
        if size is not None:
            data = data[:size].ljust(size, b"\0")
        self._rec("feature_set", data)

    def read(self, length: int, timeout_ms: int) -> bytes:
        self._push({"op": "read", "len": length, "timeout": timeout_ms})
        if 0 <= timeout_ms <= 10:
            raise DriverError(ErrorCode.TIMEOUT, "empty")
        if self.rules is not None:
            if not self.queue:
                raise DriverError(ErrorCode.TIMEOUT, "no scripted reply")
            return self._pop(length)
        return bytes(self._fill(length))

    def get_feature(self, report_id: int, length: int) -> bytes:
        self._push({"op": "feature_get", "len": length, "id": report_id})
        if self.rules is not None and self.queue:
            return self._pop(length)
        buf = self._fill(length)
        if length:
            buf[0] = report_id
        return bytes(buf)

    def get_input(self, report_id: int, length: int) -> bytes:
        if self.rules is not None and self.queue:
            self._push({"op": "input_get", "len": length, "id": report_id})
            return self._pop(length)
        self.idle_inputs += 1
        if self.idle_inputs > 3:
            return b""
        self._push({"op": "input_get", "len": length, "id": report_id})
        buf = self._fill(length)
        if length:
            buf[0] = report_id
        return bytes(buf)


def _call(drv, h, name: str, arg):
    if name == "battery":
        b: Battery = drv.battery(h)
        return {"level": b.level, "status": int(b.status), "mv": b.millivolts if b.millivolts is not None else -1}
    if name in ("sidetone", "get_sidetone"):
        s: Sidetone = drv.set_sidetone(h, arg) if name == "sidetone" else drv.get_sidetone(h)
        return {"current": s.current, "device": s.device_level}
    if name == "lights":
        return {"enabled": bool(drv.set_lights(h, bool(arg)))}
    if name == "inactive_time":
        return {"minutes": drv.set_inactive_time(h, arg)}
    if name == "chatmix":
        c: Chatmix = drv.chatmix(h)
        return {"level": c.level, "game": c.game, "chat": c.chat}
    simple = {
        "notification_sound": drv.notification_sound,
        "voice_prompts": lambda h, a: drv.set_voice_prompts(h, bool(a)),
        "rotate_to_mute": lambda h, a: drv.set_rotate_to_mute(h, bool(a)),
        "eq_preset": drv.set_eq_preset,
        "equalizer": drv.set_equalizer,
        "parametric_eq": lambda h, a: drv.set_parametric_eq(
            h, [ParametricBand(f, g, q, FilterType(t)) for f, g, q, t in a]),
        "mic_led": drv.set_mic_mute_led,
        "mic_volume": drv.set_mic_volume,
        "volume_limiter": lambda h, a: drv.set_volume_limiter(h, bool(a)),
        "bt_when_powered_on": lambda h, a: drv.set_bt_when_powered_on(h, bool(a)),
        "bt_call_volume": drv.set_bt_call_volume,
        "noise_filter": drv.set_noise_filter,
    }
    simple[name](h, arg)
    return {}


def _load() -> list[dict]:
    with gzip.open(GOLDEN, "rt") as fh:
        return json.load(fh)


class GoldenDrivers(unittest.TestCase):
    maxDiff = 4000

    @classmethod
    def setUpClass(cls) -> None:
        cls.golden = _load()
        cls.scripts = load_scripts()
        set_sleeper(lambda _s: None)  # the C++ reference ran without its pauses too
        ticks = iter(range(10**9))
        set_clock(lambda: next(ticks) / 1000.0)  # 1 ms per reading: polling loops end quickly

    @classmethod
    def tearDownClass(cls) -> None:
        set_sleeper(None)
        set_clock(None)

    def test_every_ported_driver_matches_headsetcontrol(self) -> None:
        by_name = {cls.NAME: cls for cls in REGISTRY.drivers}
        ported = 0
        for dev in self.golden:
            cls = by_name.get(dev["name"])
            if cls is None:
                continue
            ported += 1
            with self.subTest(driver=dev["name"]):
                self._check_static(cls, dev)
                self._check_calls(cls, dev)
        self.assertGreater(ported, 0)

    def _check_static(self, cls, dev) -> None:
        self.assertEqual(cls.VENDOR, dev["vendor"])
        self.assertEqual(list(cls.PIDS), dev["pids"])
        caps = {c["cap"]: (c["usagepage"], c["usageid"], c["interface"]) for c in dev["caps"]}
        mine = {c.value: (d.usage_page, d.usage, d.interface) for c, d in cls.CAPS.items()}
        self.assertEqual(mine, caps)
        if cls.EQ_FROM_DEVICE:
            self.assertIsNone(cls.EQ)
        elif dev["eq"]:
            e = cls.EQ
            self.assertEqual((e.bands, e.baseline, e.min, e.max), (dev["eq"]["bands"], dev["eq"]["baseline"],
                                                                   dev["eq"]["min"], dev["eq"]["max"]))
            self.assertAlmostEqual(e.step, dev["eq"]["step"], places=5)
        if dev["peq"] and not cls.EQ_FROM_DEVICE:
            p = cls.PEQ
            self.assertEqual((p.bands, p.freq_min, p.freq_max, p.filter_types),
                             (dev["peq"]["bands"], dev["peq"]["freq_min"], dev["peq"]["freq_max"],
                              dev["peq"]["filter_types"]))
        self.assertEqual(cls.PRESET_COUNT, len({c["arg"] for c in dev["calls"] if c["call"] == "eq_preset"}))
        presets = [(p["name"], [round(v, 4) for v in p["values"]]) for p in dev["presets"]]
        self.assertEqual([(n, [round(v, 4) for v in vals]) for n, vals in cls.EQ_PRESETS], presets)

    def _check_calls(self, cls, dev) -> None:
        drv = cls()
        groups: OrderedDict[tuple, dict] = OrderedDict()
        for call in dev["calls"]:
            groups.setdefault((call["call"], json.dumps(call["arg"])), {})[call["pattern"]] = call
        patterns = [(p, None) for p in PATTERNS]
        patterns += [(f"script:{v}", rules) for v, rules in self.scripts.get(dev["name"], [])]
        for (name, arg_s), expected in groups.items():
            arg = json.loads(arg_s)
            for pattern, rules in patterns:  # same order (and so same driver state) as the C++ run
                rec = Recorder(pattern, rules)
                try:
                    result = _call(drv, rec, name, arg)
                except DriverError:
                    result = {"error": True}
                want = expected.get(pattern)
                if want is None:
                    continue
                label = f"{dev['name']} {name}({arg_s}) [{pattern}]"
                if cls.TIMING_EXACT:
                    self.assertEqual(rec.ops, want["ops"], label)
                    self.assertEqual(rec.dropped, want["dropped"], label)
                else:
                    strip = lambda ops: [{k: v for k, v in op.items() if k != "timeout"} for op in ops]  # noqa: E731
                    self.assertEqual(strip(rec.ops), strip(want["ops"]), label)
                    self.assertEqual(rec.dropped > 0, want["dropped"] > 0, label)
                if "error" in want["result"]:
                    self.assertIn("error", result, label)
                else:
                    self.assertEqual(result, want["result"], label)


def coverage() -> tuple[set[str], set[str]]:
    names = {d["name"] for d in _load()} - {"HeadsetControl Test device"}
    ported = {cls.NAME for cls in REGISTRY.drivers}
    return ported & names, names - ported


if __name__ == "__main__":
    done, todo = coverage()
    print(f"ported {len(done)}/{len(done) + len(todo)}; missing: {sorted(todo)}")
    unittest.main()
