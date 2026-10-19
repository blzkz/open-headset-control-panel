"""Background HID service: connection management, polling, events, coalesced writes.

GUI-agnostic: state changes are reported through a :class:`ServiceListener`,
which the Qt layer adapts to signals.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum

from .battery import BatteryEstimator, BatteryState
from .device import Headset, charge_cable_connected, find_receivers
from .hidpp import FeatureId, HidppError, HidppTimeout, TransportError
from .hidpp.features import AdcBattery, EqualizerInfo
from .hidpp.protocol import Message

log = logging.getLogger(__name__)


class LinkState(Enum):
    NO_RECEIVER = "no_receiver"
    NO_PERMISSION = "no_permission"
    HEADSET_OFF = "headset_off"
    CONNECTED = "connected"


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    name: str
    model: str
    image: str
    hidraw: str
    usb_id: str
    protocol: str
    firmware: tuple[tuple[str, str], ...]
    unit_id: str
    model_id: str
    features: tuple[tuple[int, int, str], ...]
    eq_info: EqualizerInfo | None
    has_sidetone: bool
    has_equalizer: bool
    has_battery: bool


class ServiceListener:
    """Override what you need; all methods are called from the service thread."""

    def on_state(self, state: LinkState, detail: str) -> None: ...
    def on_device(self, snapshot: DeviceSnapshot) -> None: ...
    def on_battery(self, battery: BatteryState) -> None: ...
    def on_sidetone(self, level: int) -> None: ...
    def on_equalizer(self, gains: list[int]) -> None: ...
    def on_cable(self, connected: bool) -> None: ...
    def on_error(self, message: str) -> None: ...


class HeadsetService(threading.Thread):
    RESCAN_INTERVAL = 2.0
    OFFLINE_PROBE_INTERVAL = 2.5
    KEEPALIVE_INTERVAL = 6.0
    CABLE_INTERVAL = 3.0
    # A worn micro-USB plug makes the charger interface drop and re-enumerate
    # several times per second; only trust a state that stays put this long.
    CABLE_SETTLE = 12.0

    def __init__(self, listener: ServiceListener, battery_interval: float = 30.0):
        super().__init__(name="openlogi-hid", daemon=True)
        self.listener = listener
        self.battery_interval = battery_interval
        self._wake = threading.Event()
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._pending: dict[str, object] = {}
        self._desired: dict[str, object] = {}
        self._headset: Headset | None = None
        self._online = False
        self._state: LinkState | None = None
        self._estimator = BatteryEstimator()
        self._cable: bool | None = None
        self._cable_seen: bool | None = None
        self._cable_since = 0.0
        self._adc_index = -1
        self._sidetone_index = -1
        self._eq_index = -1
        self._external_events: list[Message] = []

    # -- public, thread-safe API --------------------------------------------------
    def set_sidetone(self, level: int) -> None:
        self._submit("sidetone", int(level))

    def set_equalizer(self, gains: list[int]) -> None:
        self._submit("equalizer", list(gains))

    def refresh_battery(self) -> None:
        self._submit("battery", True)

    def set_desired_state(self, sidetone: int | None, gains: list[int] | None) -> None:
        """State re-applied automatically every time the headset (re)connects."""
        with self._lock:
            self._desired = {}
            if sidetone is not None:
                self._desired["sidetone"] = int(sidetone)
            if gains is not None:
                self._desired["equalizer"] = list(gains)

    def stop(self) -> None:
        self._stop_evt.set()
        self._wake.set()

    def _submit(self, key: str, value: object) -> None:
        with self._lock:
            self._pending[key] = value  # latest wins → slider drags are coalesced
        self._wake.set()

    # -- state helpers -----------------------------------------------------------------
    def _set_state(self, state: LinkState, detail: str = "") -> None:
        if state is not self._state:
            self._state = state
            self.listener.on_state(state, detail)

    def _close(self) -> None:
        if self._headset is not None:
            try:
                self._headset.close()
            except OSError:
                pass
        self._headset = None
        self._online = False

    def _sleep(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    # -- notifications -------------------------------------------------------------------
    def _on_message(self, msg: Message) -> None:
        # Called from within request()/poll_notifications(): only queue here.
        self._external_events.append(msg)

    def _handle_events(self) -> None:
        events, self._external_events = self._external_events, []
        for msg in events:
            log.debug("event feat=%d fn=%d sw=%d %s", msg.feature_index, msg.function, msg.sw_id, msg.payload.hex())
            if not self._online:
                self._wake.set()  # any traffic while offline: try to probe right away
                continue
            if msg.feature_index == self._adc_index and msg.function == 0:
                reading = AdcBattery.parse_event(msg.payload)
                if reading.millivolts > 0:
                    self.listener.on_battery(
                        self._estimator.update(reading.millivolts, reading.charging, reading.charge_complete)
                    )
            elif msg.sw_id != 0 and msg.feature_index == self._sidetone_index and msg.function == 1:
                self.listener.on_sidetone(msg.payload[0])  # changed by another client (e.g. CLI)
            elif msg.sw_id != 0 and msg.feature_index == self._eq_index and msg.function == 3:
                info = self._headset.eq_info if self._headset else None
                n = info.band_count if info else 10
                self.listener.on_equalizer([int.from_bytes(msg.payload[1 + i: 2 + i], "big", signed=True)
                                            for i in range(n)])

    # -- main loop -------------------------------------------------------------------------
    def run(self) -> None:
        next_rescan = next_battery = next_keepalive = next_cable = 0.0
        while not self._stop_evt.is_set():
            now = time.monotonic()
            try:
                if self._headset is None:
                    if now < next_rescan:
                        self._sleep(next_rescan - now)
                        continue
                    next_rescan = now + self.RESCAN_INTERVAL
                    self._open_receiver()
                    continue

                if now >= next_cable:
                    next_cable = now + self.CABLE_INTERVAL
                    if self._update_cable(charge_cable_connected(self._headset.spec), now):
                        next_battery = 0.0  # charge state changed → re-read soon

                if not self._online:
                    if self._try_probe():
                        next_battery = 0.0
                        next_keepalive = time.monotonic() + self.KEEPALIVE_INTERVAL
                    else:
                        self._headset.dev.poll_notifications(0)
                        self._handle_events()
                        if not self._online:
                            self._sleep(self.OFFLINE_PROBE_INTERVAL)
                    continue

                self._process_pending()
                now = time.monotonic()
                if now >= next_battery:
                    next_battery = now + self.battery_interval
                    next_keepalive = now + self.KEEPALIVE_INTERVAL
                    self._read_battery()
                elif now >= next_keepalive:
                    next_keepalive = now + self.KEEPALIVE_INTERVAL
                    try:
                        self._headset.dev.ping(timeout=0.6)
                    except HidppError as exc:  # any failure here means the link is gone
                        raise HidppTimeout(str(exc)) from exc

                self._headset.dev.poll_notifications(0)
                self._handle_events()
                self._sleep(min(0.5, max(0.0, next_keepalive - time.monotonic())))
            except TransportError as exc:
                log.info("receiver gone: %s", exc)
                self._close()
                self._cable = self._cable_seen = None
                self._set_state(LinkState.NO_RECEIVER)
            except HidppTimeout:
                log.info("headset stopped answering")
                self._online = False
                self._estimator.reset()
                self._set_state(LinkState.HEADSET_OFF)
            except HidppError as exc:
                if getattr(exc, "device_unreachable", False):
                    self._online = False
                    self._set_state(LinkState.HEADSET_OFF)
                else:
                    log.warning("HID++ error: %s", exc)
                    self.listener.on_error(str(exc))
            except Exception as exc:  # keep the service alive no matter what
                log.exception("unexpected service error")
                self.listener.on_error(repr(exc))
                self._sleep(1.0)
        self._close()

    def _update_cable(self, present: bool, now: float) -> bool:
        """Debounced cable state; returns True when the reported state changed."""
        if present != self._cable_seen:
            self._cable_seen, self._cable_since = present, now
        settled = self._cable is None or now - self._cable_since >= self.CABLE_SETTLE
        if settled and present != self._cable:
            self._cable = present
            self.listener.on_cable(present)
            return True
        return False

    def _open_receiver(self) -> None:
        receivers = find_receivers()
        if not receivers:
            self._set_state(LinkState.NO_RECEIVER)
            return
        info = receivers[0]
        try:
            self._headset = Headset.open(info, on_notification=self._on_message)
        except TransportError as exc:
            self._set_state(LinkState.NO_PERMISSION, f"{info.path}: {exc.strerror or exc}")
            return
        self._wake.set()

    def _try_probe(self) -> bool:
        hs = self._headset
        assert hs is not None
        try:
            hs.probe()
        except (HidppTimeout, HidppError) as exc:
            if not isinstance(exc, HidppTimeout) and not getattr(exc, "device_unreachable", False):
                log.warning("probe failed: %s", exc)
            self._set_state(LinkState.HEADSET_OFF)
            return False
        self._online = True
        self._estimator.reset()
        self._adc_index = hs.adc_feature_index()
        self._sidetone_index = self._index_of(FeatureId.SIDETONE)
        self._eq_index = self._index_of(FeatureId.EQUALIZER)
        ident = hs.identity
        self.listener.on_device(
            DeviceSnapshot(
                name=ident.name or hs.spec.name,
                model=hs.spec.name,
                image=hs.spec.image,
                hidraw=hs.info.path,
                usb_id=hs.info.usb_id,
                protocol=f"{ident.protocol[0]}.{ident.protocol[1]}",
                firmware=tuple((fw.kind_name, fw.version) for fw in ident.firmware),
                unit_id=ident.unit_id,
                model_id=ident.model_id,
                features=tuple((idx, fid, FeatureId.label(fid)) for idx, fid, _, _ in hs.caps.features),
                eq_info=hs.eq_info,
                has_sidetone=hs.caps.sidetone,
                has_equalizer=hs.caps.equalizer,
                has_battery=hs.caps.battery,
            )
        )
        # Re-apply the active profile, then report what the device actually holds.
        with self._lock:
            for key, value in self._desired.items():
                self._pending.setdefault(key, value)
        self._process_pending()
        if hs.caps.sidetone:
            self.listener.on_sidetone(hs.sidetone.get())
        if hs.caps.equalizer:
            self.listener.on_equalizer(hs.equalizer.get())
        self._set_state(LinkState.CONNECTED)
        return True

    def _index_of(self, fid: int) -> int:
        try:
            return self._headset.dev.feature_index(fid) if self._headset else -1
        except HidppError:
            return -1

    def _process_pending(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
        hs = self._headset
        if hs is None or not pending:
            return
        if "sidetone" in pending and hs.caps.sidetone:
            level = hs.sidetone.set(pending["sidetone"])  # type: ignore[arg-type]
            self.listener.on_sidetone(level)
        if "equalizer" in pending and hs.caps.equalizer:
            gains = hs.equalizer.set(pending["equalizer"])  # type: ignore[arg-type]
            self.listener.on_equalizer(gains)
        if "battery" in pending:
            self._read_battery()

    def _read_battery(self) -> None:
        hs = self._headset
        if hs is None:
            return
        reading = hs.read_battery()
        if reading is None or reading.millivolts == 0:
            return
        if self._estimator.empty:
            # seed with a median: the ADC jitters by ±10 mV (≈ ±4 %) under charge
            samples = [reading] + [hs.read_battery() or reading for _ in range(2)]
            reading = sorted(samples, key=lambda r: r.millivolts)[1]
        self.listener.on_battery(self._estimator.update(reading.millivolts, reading.charging, reading.charge_complete))
