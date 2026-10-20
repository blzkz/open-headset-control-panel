"""HeadsetControl-port devices in the app: node selection, EQ layouts, service backend."""

from __future__ import annotations

import unittest

from openlogi.battery import BatteryState
from openlogi.drivers.base import Battery, BatteryStatus, Cap, CapDetail, Chatmix, Driver, EqInfo, Sidetone
from openlogi.drivers.hidio import HidEntry, collection_usages, select_path
from openlogi.presets import PRESETS_BY_KEY, EqLayout, fit_gains, match_preset
from openlogi.service import HeadsetService, LinkState, ServiceListener


class Descriptors(unittest.TestCase):
    def test_collection_usages_like_hidapi(self) -> None:
        desc = bytes.fromhex(
            "0643ff"  # Usage Page (0xFF43)
            "0a0202"  # Usage (0x0202)
            "a101"  # Collection (Application)
            "8511 9513 7508 1500 26ff00 0901 8100"  # report 0x11 …
            "c0"
            "050c 0901 a101 c0"  # Consumer Control
            "0b01000100"  # Usage (Generic Desktop / 0x0001, extended)
            "a101 c0"
        )
        self.assertEqual(collection_usages(desc), [(0xFF43, 0x0202), (0x000C, 0x0001), (0x0001, 0x0001)])
        self.assertEqual(collection_usages(b""), [(0, 0)])

    def test_select_path_matches_headsetcontrol(self) -> None:
        def e(path: str, iface: int, page: int, usage: int) -> HidEntry:
            return HidEntry(path, 1, 2, iface, page, usage, "", path)

        entries = [e("/dev/hidraw1", 0, 0x0C, 1), e("/dev/hidraw2", 3, 0xFF43, 0x0202), e("/dev/hidraw3", 4, 0xFF00, 1)]
        self.assertEqual(select_path(entries, CapDetail(0xFF43, 0x0202, 0)), "/dev/hidraw2")
        self.assertEqual(select_path(entries, CapDetail(0, 0, 0)), "/dev/hidraw1")  # interface 0: first entry
        self.assertEqual(select_path(entries, CapDetail(0, 0, 4)), "/dev/hidraw3")
        self.assertEqual(select_path(entries, CapDetail(0xFFAA, 1, 3)), "/dev/hidraw2")  # usage miss: interface
        self.assertIsNone(select_path(entries, CapDetail(0, 0, 7)))


class EqLayouts(unittest.TestCase):
    def test_same_band_count_maps_by_index_and_snaps(self) -> None:
        layout = EqLayout(lo=-6, hi=6, step=0.5)
        self.assertEqual(fit_gains([7, 6.3, 5.2, -9, 0, 0, 0, 0, 0, 0.26], layout),
                         [6, 6, 5, -6, 0, 0, 0, 0, 0, 0.5])

    def test_other_layouts_are_resampled_and_presets_still_match(self) -> None:
        layout = EqLayout((32, 150, 720, 3380, 16000), -7, 7, 1.0)
        gains = fit_gains(PRESETS_BY_KEY["bass_boost"].gains, layout)
        self.assertEqual(gains, [7, 4, 0, 0, 0])
        self.assertEqual(match_preset(gains, {}, layout), "bass_boost")
        self.assertEqual(match_preset([1, 1, 1, 1, 1], {}, layout), "custom")


class _Listener(ServiceListener):
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def on_state(self, state, detail):
        self.events.append(("state", state))

    def on_device(self, snapshot):
        self.events.append(("device", snapshot))

    def on_battery(self, battery):
        self.events.append(("battery", battery))

    def on_sidetone(self, level):
        self.events.append(("sidetone", level))

    def on_equalizer(self, gains):
        self.events.append(("equalizer", gains))

    def on_control(self, name, value):
        self.events.append(("control", name, value))

    def on_chatmix(self, level, game, chat):
        self.events.append(("chatmix", level, game, chat))


class _FakeDriver(Driver):
    NAME = "Fake Headset"
    VENDOR, PIDS = 0x1038, (0x1234,)
    CAPS = {cap: CapDetail() for cap in (Cap.BATTERY, Cap.SIDETONE, Cap.EQUALIZER, Cap.CHATMIX, Cap.VOICE_PROMPTS,
                                         Cap.MIC_VOLUME, Cap.EQ_PRESET)}
    EQ = EqInfo(5, 0, 0.5, -6, 6)
    EQ_PRESETS = (("Flat", (0.0,) * 5), ("Bass", (3.0, 2.0, 0.0, 0.0, 0.0)))
    PRESET_COUNT = 2

    def __init__(self, product_id: int = 0) -> None:
        super().__init__(product_id)
        self.status = BatteryStatus.AVAILABLE
        self.calls: list[tuple] = []

    def battery(self, h):
        return Battery(-1 if self.status is BatteryStatus.CHARGING else 80, self.status)

    def set_sidetone(self, h, level):
        self.calls.append(("sidetone", level))
        return Sidetone(level)

    def set_equalizer(self, h, bands):
        self.calls.append(("equalizer", bands))

    def chatmix(self, h):
        return Chatmix(32, 100, 50)

    def set_voice_prompts(self, h, on):
        self.calls.append(("voice_prompts", on))

    def set_mic_volume(self, h, volume):
        self.calls.append(("mic_volume", volume))


class _FakeSession:
    def __init__(self) -> None:
        self.driver = _FakeDriver()
        self.caps = frozenset(self.driver.CAPS)
        self.device = type("Dev", (), {"name": "Fake Headset", "usb_id": "1038:1234", "vendor": "SteelSeries",
                                       "entries": []})()

    def path_for(self, cap):
        return "/dev/hidraw42"

    def call(self, cap, fn, *args):
        return fn(None, *args)


class DriverBackend(unittest.TestCase):
    def setUp(self) -> None:
        self.listener = _Listener()
        self.svc = HeadsetService(self.listener)
        self.svc._sleep = lambda _s: None
        self.session = _FakeSession()
        self.svc._session = self.session
        self.svc.set_desired_state(50, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], controls={"voice_prompts": True,
                                                                              "eq_preset": 1})

    def test_probe_reports_device_then_battery_and_reapplies_profile(self) -> None:
        self.assertTrue(self.svc._driver_probe())
        kinds = [e[0] for e in self.listener.events]
        self.assertLess(kinds.index("device"), kinds.index("battery"))
        snap = next(e[1] for e in self.listener.events if e[0] == "device")
        self.assertEqual(snap.backend, "headsetcontrol")
        self.assertEqual(snap.eq_info.band_count, 5)
        self.assertEqual(snap.eq_step, 0.5)
        self.assertEqual(snap.eq_presets, ("Flat", "Bass"))
        self.assertEqual(set(snap.controls), {"voice_prompts", "mic_volume", "eq_preset"})
        battery = next(e[1] for e in self.listener.events if e[0] == "battery")
        self.assertEqual(battery, BatteryState(80, 0, False, False, estimates=False))
        calls = self.session.driver.calls
        self.assertIn(("sidetone", 64), calls)  # UI 0-100 → HeadsetControl 0-128
        self.assertIn(("voice_prompts", True), calls)
        self.assertIn(("equalizer", [1, 3.5, 5.5, 6, 6]), calls)  # 10-band profile resampled to the 5 bands
        self.assertNotIn("eq_preset", [c[0] for c in calls])  # momentary: never re-applied
        self.assertIn(("state", LinkState.CONNECTED), self.listener.events)

    def test_unavailable_battery_means_headset_off(self) -> None:
        self.session.driver.status = BatteryStatus.UNAVAILABLE
        self.assertFalse(self.svc._driver_probe())
        self.assertFalse(any(e[0] == "device" for e in self.listener.events))

    def test_unknown_level_while_charging_keeps_last_percent(self) -> None:
        self.svc._driver_probe()
        self.session.driver.status = BatteryStatus.CHARGING
        state = self.svc._driver_battery()
        self.assertEqual((state.percent, state.charging), (80, True))

    def test_controls_and_chatmix(self) -> None:
        self.svc._driver_probe()
        self.listener.events.clear()
        self.svc.set_control("mic_volume", 100)
        self.svc._process_pending()
        self.svc._driver_chatmix()
        self.svc._driver_chatmix()  # unchanged: reported once
        self.assertIn(("mic_volume", 100), self.session.driver.calls)
        self.assertEqual([e for e in self.listener.events if e[0] in ("control", "chatmix")],
                         [("control", "mic_volume", 100), ("chatmix", 32, 100, 50)])


if __name__ == "__main__":
    unittest.main()
