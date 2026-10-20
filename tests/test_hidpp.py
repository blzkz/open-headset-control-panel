"""Protocol tests against a simulated PRO X Wireless (no hardware needed)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openlogi.battery import BatteryEstimator, percent_from_millivolts
from openlogi.config import Config, Profile
from openlogi.hidpp.features import AdcBattery, Equalizer, Sidetone, read_firmware, read_name
from openlogi.hidpp.features import DeviceIdentity
from openlogi.hidpp.protocol import (
    FeatureId,
    FeatureNotSupported,
    Hidpp20Device,
    HidppDeviceError,
    HidppTimeout,
    Message,
)
from openlogi.presets import CUSTOM_KEY, PRESETS_BY_KEY, match_preset
from openlogi.audio.filterchain import spa_value
from openlogi.audio.pulse import is_relevant_event

FEATURES = [0x0000, 0x0001, 0x0003, 0x0005, 0x8310, 0x8300, 0x1F20]
FREQS = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
NAME = b"PRO X Wireless Gaming Headset"


class FakeHeadset:
    """Implements the subset of HID++ 2.0 the PRO X answers, byte-exact with the real device."""

    def __init__(self, online: bool = True, features: list[int] | None = None):
        self.online = online
        self.features = list(features or FEATURES)
        self.auto_off = 30
        self.leds: list[bytes] = []
        self.sidetone = 0
        self.gains = [0] * 10
        self.queue: list[bytes] = []
        self.writes: list[bytes] = []

    # transport API
    def write(self, data: bytes) -> None:
        self.writes.append(data)
        if self.online:
            self.queue.append(self._answer(data))

    def read(self, timeout: float) -> bytes | None:
        return self.queue.pop(0) if self.queue else None

    def close(self) -> None:
        pass

    def _reply(self, req: bytes, payload: bytes) -> bytes:
        return (req[:4] + payload).ljust(20, b"\0")

    def _error(self, req: bytes, code: int) -> bytes:
        return bytes([0x11, 0xFF, 0xFF, req[2], req[3], code]).ljust(20, b"\0")

    def _answer(self, req: bytes) -> bytes:
        idx, fn, p = req[2], req[3] >> 4, req[4:]
        if idx >= len(self.features):
            return self._error(req, 6)
        fid = self.features[idx]
        if fid == 0x0000:
            if fn == 0:
                want = int.from_bytes(p[0:2], "big")
                return self._reply(req, bytes([self.features.index(want) if want in self.features else 0]))
            if fn == 1:
                return self._reply(req, bytes([4, 2, p[2]]))
        if fid == 0x0001:
            if fn == 0:
                return self._reply(req, bytes([len(self.features) - 1]))
            if fn == 1:
                return self._reply(req, self.features[p[0]].to_bytes(2, "big") + b"\0\0")
        if fid == 0x0003:
            if fn == 0:
                return self._reply(req, bytes.fromhex("01ffffffff0003000000000aba0000"))
            if fn == 1:
                return self._reply(req, bytes.fromhex("0055312012010203010aba"))
        if fid == 0x0005:
            if fn == 0:
                return self._reply(req, bytes([len(NAME)]))
            if fn == 1:
                return self._reply(req, NAME[p[0]: p[0] + 16])
            if fn == 2:
                return self._reply(req, b"\x08")
        if fid == 0x8310:
            if fn == 0:
                return self._reply(req, bytes([10, 12, 1, 0, 0]))
            if fn == 1:
                start = p[0]
                if start >= 10:
                    return self._error(req, 2)
                chunk = b"".join(f.to_bytes(2, "big") for f in FREQS[start: start + 7])
                return self._reply(req, bytes([start]) + chunk)
            if fn == 2:
                if p[0] != 0:
                    return self._error(req, 2)
                return self._reply(req, bytes(g & 0xFF for g in self.gains))
            if fn == 3:
                gains = [int.from_bytes(p[1 + i: 2 + i], "big", signed=True) for i in range(10)]
                if p[0] != 2 or any(abs(g) > 12 for g in gains):
                    return self._error(req, 2)
                self.gains = gains
                return self._reply(req, p[:11])
        if fid == 0x8300:
            if fn == 0:
                return self._reply(req, bytes([self.sidetone]))
            if fn == 1:
                self.sidetone = p[0]
                return self._reply(req, bytes([self.sidetone]))
        if fid == 0x1F20 and fn == 0:
            return self._reply(req, bytes([0x0F, 0x1C, 0x03]))
        if fid == 0x1F20 and fn == 1:
            return self._reply(req, bytes([self.auto_off]))
        if fid == 0x1F20 and fn == 2:
            self.auto_off = p[0]
            return self._reply(req, bytes([self.auto_off]))
        if fid == 0x1004 and fn == 1:
            return self._reply(req, bytes([64, 0x04, 0x00, 0x00]))
        if fid == 0x8070 and fn == 3:
            self.leds.append(bytes(p[:9]))
            return self._reply(req, b"")
        return self._error(req, 7)


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeHeadset()
        self.dev = Hidpp20Device(self.fake)  # type: ignore[arg-type]

    def test_ping_and_feature_discovery(self) -> None:
        self.assertEqual(self.dev.ping(), (4, 2))
        feats = self.dev.enumerate_features()
        self.assertEqual([f for _, f, _, _ in feats], FEATURES)
        self.assertEqual(self.dev.feature_index(FeatureId.EQUALIZER), 4)

    def test_unsupported_feature(self) -> None:
        with self.assertRaises(FeatureNotSupported):
            self.dev.feature_index(FeatureId.UNIFIED_BATTERY)
        self.assertFalse(self.dev.has_feature(FeatureId.UNIFIED_BATTERY))

    def test_device_error(self) -> None:
        with self.assertRaises(HidppDeviceError) as ctx:
            self.dev.request(9, 0)
        self.assertEqual(ctx.exception.code, 6)

    def test_offline_timeout(self) -> None:
        dev = Hidpp20Device(FakeHeadset(online=False), )  # type: ignore[arg-type]
        with self.assertRaises(HidppTimeout):
            dev.ping(timeout=0.01)

    def test_identity(self) -> None:
        name, kind = read_name(self.dev)
        self.assertEqual((name, kind), (NAME.decode(), 8))
        ident = DeviceIdentity()
        read_firmware(self.dev, ident)
        self.assertEqual(ident.firmware[0].version, "U1 12.01.B0203")
        self.assertEqual(ident.firmware[0].transport_pid, 0x0ABA)

    def test_equalizer_roundtrip_and_clamp(self) -> None:
        eq = Equalizer(self.dev)
        info = eq.info()
        self.assertEqual(info.frequencies, tuple(FREQS))
        self.assertEqual((info.db_min, info.db_max), (-12, 12))
        applied = eq.set([20, -20, 3, 0, 0, 0, 0, 0, 0, -1])
        self.assertEqual(applied[:3], [12, -12, 3])
        self.assertEqual(eq.get(), applied)
        with self.assertRaises(ValueError):
            eq.set([0] * 9)

    def test_sidetone(self) -> None:
        st = Sidetone(self.dev)
        self.assertEqual(st.set(150), 100)
        self.assertEqual(st.get(), 100)

    def test_battery(self) -> None:
        r = AdcBattery(self.dev).read()
        self.assertEqual(r.millivolts, 3868)
        self.assertTrue(r.charging)

    def test_notifications_are_dispatched(self) -> None:
        seen: list[Message] = []
        self.dev.on_notification = seen.append
        event = bytes([0x11, 0xFF, 0x06, 0x00, 0x0F, 0x00, 0x01]).ljust(20, b"\0")
        self.fake.queue.append(event)
        self.dev.poll_notifications(0)
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].is_notification)


class MultiModelTests(unittest.TestCase):
    def _headset(self, pid: int, fake: FakeHeadset):
        from openlogi.device import Headset
        from openlogi.hidpp.transport import HidrawInfo

        hs = Headset(HidrawInfo("/dev/fake", 0x046D, pid, "fake", "", True, True), Hidpp20Device(fake))
        hs.probe()
        return hs

    def test_pro_x_capabilities(self) -> None:
        hs = self._headset(0x0ABA, FakeHeadset())
        self.assertTrue(hs.caps.equalizer and hs.caps.sidetone and hs.caps.auto_off)
        self.assertFalse(hs.caps.lights)
        self.assertEqual(hs.auto_off.set(10), 10)
        self.assertEqual(hs.auto_off.get(), 10)

    def test_g935_like_device(self) -> None:
        fake = FakeHeadset(features=[0x0000, 0x0001, 0x0005, 0x8070, 0x1004, 0x8300])
        hs = self._headset(0x0A87, fake)
        self.assertEqual(hs.spec.key, "g933-family")
        self.assertFalse(hs.caps.equalizer)
        self.assertTrue(hs.caps.lights and hs.caps.sidetone)
        self.assertFalse(hs.caps.auto_off)  # no ADC feature → no timer
        reading = hs.read_battery()
        self.assertEqual((reading.percent, reading.charging), (64, False))
        from openlogi.hidpp.features import LightMode
        hs.leds.set_zone(1, LightMode.BREATHING, (0, 0xB6, 0xFF))
        self.assertEqual(fake.leds[-1], bytes([1, 2, 0x00, 0xB6, 0xFF, 0x0F, 0xA0, 0x00, 0x64]))

    def test_udev_rules_cover_every_model(self) -> None:
        from openlogi.models import ALL_PIDS
        from openlogi.permissions import RULE

        packaged = (Path(__file__).parent.parent / "packaging" / "70-openlogi-headset.rules").read_text()
        for pid in ALL_PIDS:
            self.assertIn(f"{pid:04x}", packaged)
            self.assertIn(f"{pid:04x}", RULE)

    def test_receiver_selection_prefers_vendor_collection(self) -> None:
        from unittest import mock

        from openlogi import device
        from openlogi.hidpp.transport import HidrawInfo

        nodes = [
            HidrawInfo("/dev/hidraw1", 0x046D, 0x0A66, "G533", "usb-1-2/input0", True, False),
            HidrawInfo("/dev/hidraw2", 0x046D, 0x0A66, "G533", "usb-1-2/input3", True, True),
            HidrawInfo("/dev/hidraw3", 0x046D, 0xC52B, "Unifying", "usb-1-3/input2", True, True),
        ]
        with mock.patch.object(device, "enumerate_hidraw", return_value=nodes):
            found = device.find_receivers()
        self.assertEqual([n.path for n in found], ["/dev/hidraw2"])


class BatteryTests(unittest.TestCase):
    def test_curve_bounds_and_monotonic(self) -> None:
        self.assertEqual(percent_from_millivolts(3000), 0)
        self.assertEqual(percent_from_millivolts(4300), 100)
        values = [percent_from_millivolts(mv) for mv in range(3300, 4200, 10)]
        self.assertEqual(values, sorted(values))

    def test_real_plug_unplug_trace_has_no_jumps(self) -> None:
        trace = Path(__file__).with_name("data") / "battery_trace.txt"
        rows = [tuple(int(x, 0) for x in line.split()) for line in trace.read_text().splitlines()
                if line and not line.startswith("#")]
        est = BatteryEstimator()
        shown = [est.update(mv, bool(flags & 2), now=1000.0 + t) for t, mv, flags in rows]
        steps = [abs(a.percent - b.percent) for a, b in zip(shown, shown[1:], strict=False)]
        self.assertLessEqual(max(steps), 2, "gauge must not jump on plug/unplug glitches")
        self.assertTrue(30 <= shown[-1].percent <= 60)
        self.assertTrue(shown[-1].charging)
        flips = sum(1 for a, b in zip(shown, shown[1:], strict=False) if a.charging != b.charging)
        self.assertLessEqual(flips, 2, "power-flag glitches must be debounced")

    def test_charging_advances_with_time_and_discharge_is_monotonic(self) -> None:
        est = BatteryEstimator()
        t = 0.0
        est.update(3805, False, now=t)
        start = est.update(3805, False, now=t + 60).percent
        for i in range(1, 40):  # one hour on the cable; voltage reads high but is ignored
            s = est.update(3980, True, now=t + 60 + i * 90)
        self.assertGreater(s.percent, start + 25)
        self.assertLess(s.percent, start + 40)
        # unplugged: voltage noise up and down never raises the gauge
        t = t + 60 + 40 * 90
        last = 101
        for i, mv in enumerate([3900, 3905, 3898, 3910, 3895, 3902] * 5):
            st = est.update(mv, False, now=t + 60 + i * 10)
            if i > 6:
                self.assertLessEqual(st.percent, last)
                last = st.percent

    def test_stale_saved_value_is_remeasured(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "battery.json"
            f.write_text('{"percent": 50.0, "t": 1000.0}')  # closed at 50 % last night
            fresh = BatteryEstimator(state_file=f).update(4120, False, now=1000.0 + 9 * 3600)
            self.assertGreaterEqual(fresh.percent, 90)  # charged overnight: trust the cell
            f.write_text('{"percent": 50.0, "t": 1000.0}')
            recent = BatteryEstimator(state_file=f).update(3830, False, now=1000.0 + 60)
            self.assertEqual(recent.percent, 50)  # restarted a minute later: keep the anchor

    def test_charge_complete_flag_means_full(self) -> None:
        st = BatteryEstimator().update(4120, True, complete=True)
        self.assertTrue(st.full)
        self.assertEqual(st.percent, 100)

    def test_state_persists(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "battery.json"
            a = BatteryEstimator(state_file=f)
            a.update(3880, False)
            b = BatteryEstimator(state_file=f)
            self.assertFalse(b.empty)
            self.assertEqual(b.update(3880, True).percent, a.update(3880, False).percent)


class ConfigTests(unittest.TestCase):
    def test_roundtrip_and_unknown_keys(self) -> None:
        cfg = Config()
        cfg.add_profile("FPS", copy_from="Default").eq_gains = [1] * 10
        data = cfg.to_json()
        data["future_key"] = 1
        data["profiles"]["FPS"]["voice"]["new_option"] = True
        back = Config.from_json(data)
        self.assertEqual(back.profiles["FPS"].eq_gains, [1] * 10)
        self.assertIsInstance(back.profiles["FPS"], Profile)

    def test_profile_rename_delete(self) -> None:
        cfg = Config()
        cfg.add_profile("A")
        cfg.active_profile = "A"
        cfg.rename_profile("A", "B")
        self.assertEqual(cfg.active_profile, "B")
        cfg.delete_profile("B")
        self.assertEqual(cfg.active_profile, "Default")
        cfg.delete_profile("Default")  # last profile is never deleted
        self.assertIn("Default", cfg.profiles)


class MiscTests(unittest.TestCase):
    def test_presets(self) -> None:
        self.assertEqual(match_preset(list(PRESETS_BY_KEY["bass_boost"].gains)), "bass_boost")
        self.assertEqual(match_preset([5] * 10), CUSTOM_KEY)
        self.assertEqual(match_preset([5] * 10, {"mine": [5] * 10}), "user:mine")
        for p in PRESETS_BY_KEY.values():
            self.assertEqual(len(p.gains), 10)
            self.assertTrue(all(-12 <= g <= 12 for g in p.gains))

    def test_pactl_event_filter(self) -> None:
        relevant = ["Event 'change' on source #58", "Event 'change' on sink #57", "Event 'change' on card #46",
                    "Event 'change' on server #-1"]
        noise = ["Event 'new' on source-output #5", "Event 'remove' on sink-input #3", "Event 'new' on client #9"]
        self.assertTrue(all(is_relevant_event(e) for e in relevant))
        self.assertFalse(any(is_relevant_event(e) for e in noise))

    def test_spa_json(self) -> None:
        self.assertEqual(spa_value({"a.b": [1, "x", True]}), '{ "a.b" = [ 1 "x" true ] }')


if __name__ == "__main__":
    unittest.main()
