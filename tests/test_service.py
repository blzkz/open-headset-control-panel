"""HeadsetService lifecycle against the simulated headset."""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from openlogi import service as service_mod
from openlogi.device import Headset
from openlogi.hidpp import HidrawInfo
from openlogi.hidpp.protocol import Hidpp20Device
from openlogi.service import HeadsetService, LinkState, ServiceListener

from test_hidpp import FakeHeadset

INFO = HidrawInfo("/dev/hidraw-fake", 0x046D, 0x0ABA, "fake", "", True)


class Recorder(ServiceListener):
    def __init__(self):
        self.states: list[LinkState] = []
        self.sidetone: list[int] = []
        self.eq: list[list[int]] = []
        self.battery = []
        self.cv = threading.Condition()

    def _note(self):
        with self.cv:
            self.cv.notify_all()

    def on_state(self, state, detail):
        self.states.append(state)
        self._note()

    def on_sidetone(self, level):
        self.sidetone.append(level)
        self._note()

    def on_equalizer(self, gains):
        self.eq.append(gains)
        self._note()

    def on_battery(self, b):
        self.battery.append(b)
        self._note()

    def wait(self, pred, timeout=5.0):
        end = time.monotonic() + timeout
        with self.cv:
            while not pred():
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self.cv.wait(left)
        return True


class ServiceTests(unittest.TestCase):
    def test_connect_apply_offline_reconnect(self):
        fake = FakeHeadset()
        rec = Recorder()
        opener = lambda info, on_notification=None, sw_id=0x0A: Headset(  # noqa: E731
            info, Hidpp20Device(fake, on_notification=on_notification, sw_id=sw_id))
        with mock.patch.object(service_mod, "find_receivers", return_value=[INFO]), \
                mock.patch.object(service_mod.Headset, "open", side_effect=opener), \
                mock.patch.object(service_mod, "charge_cable_connected", return_value=False):
            svc = HeadsetService(rec, battery_interval=60)
            svc.OFFLINE_PROBE_INTERVAL = 0.05
            svc.KEEPALIVE_INTERVAL = 0.1
            svc.set_desired_state(42, [3] * 10)
            svc.start()
            try:
                self.assertTrue(rec.wait(lambda: LinkState.CONNECTED in rec.states))
                self.assertEqual(fake.sidetone, 42)
                self.assertEqual(fake.gains, [3] * 10)
                self.assertTrue(rec.wait(lambda: rec.battery))

                # slider storm is coalesced: the device ends at the last value
                for v in range(0, 101, 5):
                    svc.set_sidetone(v)
                self.assertTrue(rec.wait(lambda: fake.sidetone == 100))

                fake.online = False
                self.assertTrue(rec.wait(lambda: rec.states[-1] is LinkState.HEADSET_OFF))
                fake.sidetone, fake.gains = 0, [0] * 10  # headset forgot its RAM settings
                fake.online = True
                self.assertTrue(rec.wait(lambda: rec.states[-1] is LinkState.CONNECTED))
                self.assertTrue(rec.wait(lambda: fake.sidetone == 100 or fake.sidetone == 42))
                self.assertEqual(fake.gains, [3] * 10)
            finally:
                svc.stop()
                svc.join(2)


if __name__ == "__main__":
    unittest.main()


class CableDebounceTests(unittest.TestCase):
    def test_flapping_cable_is_ignored(self):
        rec = Recorder()
        rec.cables = []
        rec.on_cable = rec.cables.append
        svc = HeadsetService(rec)
        t = 100.0
        svc._update_cable(True, t)            # first observation is reported at once
        for i in range(20):                   # re-enumerating every 0.5 s
            svc._update_cable(i % 2 == 0, t + 0.5 * i)
        self.assertEqual(rec.cables, [True])
        svc._update_cable(False, t + 20)      # really unplugged: settles after CABLE_SETTLE
        svc._update_cable(False, t + 20 + svc.CABLE_SETTLE)
        self.assertEqual(rec.cables, [True, False])
