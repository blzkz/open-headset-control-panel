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

from .battery import CACHE_DIR, BatteryEstimator, BatteryState
from .device import LOGITECH_VID, BatterySource, Headset, charge_cable_connected, find_receivers
from .drivers.base import BatteryStatus, Cap, DriverError, ErrorCode
from .drivers.session import DriverSession, find_driver_devices
from .hidpp import FeatureId, HidppDeviceError, HidppError, HidppTimeout, TransportError
from .hidpp.features import AdcBattery, EqualizerInfo, LevelReading, LightMode
from .hidpp.protocol import Message
from .models import BY_PID
from .presets import EqLayout, default_freqs, fit_gains

log = logging.getLogger(__name__)


# Driver capabilities the UI shows as generic controls: name -> (capability, setter, value type).
CONTROLS: dict[str, tuple[Cap, str, type]] = {
    "lights": (Cap.LIGHTS, "set_lights", bool),
    "voice_prompts": (Cap.VOICE_PROMPTS, "set_voice_prompts", bool),
    "rotate_to_mute": (Cap.ROTATE_TO_MUTE, "set_rotate_to_mute", bool),
    "volume_limiter": (Cap.VOLUME_LIMITER, "set_volume_limiter", bool),
    "bt_when_powered_on": (Cap.BT_WHEN_POWERED_ON, "set_bt_when_powered_on", bool),
    "bt_call_volume": (Cap.BT_CALL_VOLUME, "set_bt_call_volume", int),
    "noise_filter": (Cap.NOISE_FILTER, "set_noise_filter", int),
    "mic_volume": (Cap.MIC_VOLUME, "set_mic_volume", int),
    "mic_mute_led": (Cap.MIC_MUTE_LED, "set_mic_mute_led", int),
    "eq_preset": (Cap.EQ_PRESET, "set_eq_preset", int),
    "notification_sound": (Cap.NOTIFICATION_SOUND, "notification_sound", int),
}
# Momentary actions: sent when asked, never stored or re-applied on reconnect.
ACTIONS = frozenset({"notification_sound", "eq_preset"})

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
    model_key: str = ""
    verified: bool = True
    wireless: bool = True
    has_auto_off: bool = False
    light_zones: tuple[tuple[int, str], ...] = ()
    battery_source: str = "none"
    backend: str = "hidpp"  # or "headsetcontrol": a driver ported from HeadsetControl
    vendor: str = "Logitech"
    controls: tuple[str, ...] = ()  # names from CONTROLS this device supports
    eq_presets: tuple[str, ...] = ()  # onboard presets of the headset itself
    eq_step: float = 1.0
    has_chatmix: bool = False


class ServiceListener:
    """Override what you need; all methods are called from the service thread."""

    def on_state(self, state: LinkState, detail: str) -> None: ...
    def on_device(self, snapshot: DeviceSnapshot) -> None: ...
    def on_battery(self, battery: BatteryState) -> None: ...
    def on_sidetone(self, level: int) -> None: ...
    def on_equalizer(self, gains: list[int]) -> None: ...
    def on_cable(self, connected: bool) -> None: ...
    def on_auto_off(self, minutes: int) -> None: ...
    def on_control(self, name: str, value: object) -> None: ...
    def on_chatmix(self, level: int, game: int, chat: int) -> None: ...
    def on_error(self, message: str) -> None: ...


class HeadsetService(threading.Thread):
    RESCAN_INTERVAL = 2.0
    OFFLINE_PROBE_INTERVAL = 2.5
    KEEPALIVE_INTERVAL = 6.0
    CABLE_INTERVAL = 3.0
    BATTERY_RETRY = 3.0  # seconds between reads while a plug/unplug is being confirmed
    CHATMIX_INTERVAL = 1.0
    # A worn micro-USB plug makes the charger interface drop and re-enumerate
    # several times per second; only trust a state that stays put this long.
    CABLE_SETTLE = 12.0

    def __init__(self, listener: ServiceListener, battery_interval: float = 30.0):
        super().__init__(name="open-headset-hid", daemon=True)
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
        self._estimator = BatteryEstimator(state_file=CACHE_DIR / "battery.json")
        self._cable: bool | None = None
        self._cable_seen: bool | None = None
        self._cable_since = 0.0
        self._adc_index = -1
        self._sidetone_index = -1
        self._eq_index = -1
        self._external_events: list[Message] = []
        self._battery_retry = False
        self._session: DriverSession | None = None  # a HeadsetControl-port device instead of HID++
        self._next_probe = self._next_battery = self._next_chatmix = 0.0
        self._last_percent = -1
        self._chatmix: tuple[int, int, int] | None = None
        self._eq_layout: EqLayout | None = None

    # -- public, thread-safe API --------------------------------------------------
    def set_sidetone(self, level: int) -> None:
        self._submit("sidetone", int(level))

    def set_equalizer(self, gains: list[int]) -> None:
        self._submit("equalizer", list(gains))

    def refresh_battery(self) -> None:
        self._submit("battery", True)

    def set_auto_off(self, minutes: int) -> None:
        self._submit("auto_off", int(minutes))

    def set_lights(self, zones: dict[int, tuple[int, tuple[int, int, int], int]]) -> None:
        """``{zone: (mode, (r, g, b), brightness)}``."""
        self._submit("lights", dict(zones))

    def set_control(self, name: str, value: object) -> None:
        """A driver control (see CONTROLS), e.g. ``set_control("voice_prompts", True)``."""
        self._submit(f"ctl:{name}", value)

    def set_desired_state(self, sidetone: int | None, gains: list[float] | None,
                          auto_off: int | None = None,
                          lights: dict[int, tuple[int, tuple[int, int, int], int]] | None = None,
                          controls: dict[str, object] | None = None) -> None:
        """State re-applied automatically every time the headset (re)connects."""
        with self._lock:
            self._desired = {}
            if sidetone is not None:
                self._desired["sidetone"] = int(sidetone)
            if gains is not None:
                self._desired["equalizer"] = list(gains)
            if auto_off is not None:
                self._desired["auto_off"] = int(auto_off)
            if lights:
                self._desired["lights"] = dict(lights)
            for name, value in (controls or {}).items():
                if name in CONTROLS and name not in ACTIONS:
                    self._desired[f"ctl:{name}"] = value

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
        self._session = None
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
                    self._feed_battery(reading)
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
                if self._session is not None:
                    self._driver_tick()
                    continue
                if self._headset is None:
                    if now < next_rescan:
                        self._sleep(next_rescan - now)
                        continue
                    next_rescan = now + self.RESCAN_INTERVAL
                    self._open_receiver()
                    continue

                if now >= next_cable:
                    next_cable = now + self.CABLE_INTERVAL
                    self._update_cable(charge_cable_connected(self._headset.spec), now)

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
                    self._read_battery()
                    wait = self.BATTERY_RETRY if self._battery_retry else self.battery_interval
                    next_battery = now + wait
                    next_keepalive = now + self.KEEPALIVE_INTERVAL
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
            except DriverError as exc:
                if exc.code in (ErrorCode.TIMEOUT, ErrorCode.OFFLINE):
                    log.info("headset stopped answering: %s", exc)
                    self._online = False
                    self._next_probe = time.monotonic() + self.OFFLINE_PROBE_INTERVAL
                    self._set_state(LinkState.HEADSET_OFF)
                else:
                    log.warning("driver error: %s", exc)
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
            self._open_driver_device()
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
        if self._estimator.curve is not hs.spec.curve:
            self._estimator = BatteryEstimator(state_file=CACHE_DIR / f"battery-{hs.spec.key}.json",
                                               curve=hs.spec.curve)
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
                has_battery=hs.caps.battery is not BatterySource.NONE,
                model_key=hs.spec.key,
                verified=hs.spec.verified,
                wireless=hs.spec.wireless,
                has_auto_off=hs.caps.auto_off,
                light_zones=hs.spec.light_zones if hs.caps.lights else (),
                battery_source=hs.caps.battery.value,
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
        if hs.caps.auto_off:
            self.listener.on_auto_off(hs.auto_off.get())
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
        if self._session is not None:
            if pending:
                self._driver_apply(pending)
            return
        hs = self._headset
        if hs is None or not pending:
            return
        if "sidetone" in pending and hs.caps.sidetone:
            level = hs.sidetone.set(pending["sidetone"])  # type: ignore[arg-type]
            self.listener.on_sidetone(level)
        if "equalizer" in pending and hs.caps.equalizer:
            gains = hs.equalizer.set([int(round(g)) for g in pending["equalizer"]])  # type: ignore[union-attr]
            self.listener.on_equalizer(gains)
        if "auto_off" in pending and hs.caps.auto_off:
            self.listener.on_auto_off(hs.auto_off.set(pending["auto_off"]))  # type: ignore[arg-type]
        if "lights" in pending and hs.caps.lights:
            for zone, (mode, rgb, brightness) in pending["lights"].items():  # type: ignore[union-attr]
                if any(z == zone for z, _ in hs.spec.light_zones):
                    hs.leds.set_zone(zone, LightMode(mode), rgb, brightness)
        if "battery" in pending:
            self._read_battery()

    def _read_battery(self) -> None:
        hs = self._headset
        if hs is None:
            return
        try:
            reading = hs.read_battery()
        except HidppDeviceError as exc:
            # the ADC answers LOGITECH_INTERNAL while power is being switched: not an error
            log.debug("battery read skipped: %s", exc)
            self._battery_retry = True
            return
        if reading is None:
            return
        if isinstance(reading, LevelReading):  # the device reports a percentage itself
            self.listener.on_battery(BatteryState(reading.percent, 0, reading.charging, reading.full))
            return
        if reading.millivolts == 0:
            return
        self._feed_battery(reading)

    def _feed_battery(self, reading) -> None:
        state = self._estimator.update(reading.millivolts, reading.charging, reading.charge_complete)
        # a power flag change is being debounced: confirm it quickly instead of in 30 s
        self._battery_retry = self._estimator.transition_pending
        self.listener.on_battery(state)

    # -- HeadsetControl-port devices ----------------------------------------------------------
    def _open_driver_device(self) -> None:
        devices = find_driver_devices(skip=((LOGITECH_VID, pid) for pid in BY_PID))
        if not devices:
            self._set_state(LinkState.NO_RECEIVER)
            return
        session = DriverSession(devices[0])
        try:
            session.check_access()
        except TransportError as exc:
            self._set_state(LinkState.NO_PERMISSION, f"{session.device.usb_id}: {exc.strerror or exc}")
            return
        log.info("using HeadsetControl driver %r for %s", session.driver.NAME, session.device.usb_id)
        self._session = session
        self._online = False
        self._next_probe = 0.0
        self._last_percent = -1
        self._chatmix = None
        self._wake.set()

    def _driver_tick(self) -> None:
        s = self._session
        assert s is not None
        now = time.monotonic()
        if not self._online:
            if now < self._next_probe:
                self._sleep(self._next_probe - now)
                return
            self._next_probe = now + self.OFFLINE_PROBE_INTERVAL
            if not self._driver_probe():
                self._set_state(LinkState.HEADSET_OFF)
            return
        self._process_pending()
        now = time.monotonic()
        if Cap.BATTERY in s.caps and now >= self._next_battery:
            self._next_battery = now + self.battery_interval
            if self._driver_battery() is None:
                raise DriverError(ErrorCode.OFFLINE, "battery unavailable")
        if Cap.CHATMIX in s.caps and now >= self._next_chatmix:
            self._next_chatmix = now + self.CHATMIX_INTERVAL
            self._driver_chatmix()
        wait = self.battery_interval
        if Cap.BATTERY in s.caps:
            wait = min(wait, self._next_battery - time.monotonic())
        if Cap.CHATMIX in s.caps:
            wait = min(wait, self._next_chatmix - time.monotonic())
        self._sleep(max(0.05, min(0.5, wait)))

    def _driver_battery(self, emit: bool = True) -> BatteryState | None:
        """Read (and report) the battery; None when the headset is not reachable."""
        s = self._session
        assert s is not None
        b = s.call(Cap.BATTERY, s.driver.battery)
        if b.status in (BatteryStatus.UNAVAILABLE, BatteryStatus.TIMEOUT, BatteryStatus.HIDERROR):
            return None
        charging = b.status is BatteryStatus.CHARGING
        if b.level >= 0:
            self._last_percent = min(100, b.level)
        state = BatteryState(self._last_percent, b.millivolts or 0, charging, charging and self._last_percent >= 100,
                             estimates=False)
        if emit:
            self.listener.on_battery(state)
        return state

    def _driver_chatmix(self) -> None:
        s = self._session
        assert s is not None
        c = s.call(Cap.CHATMIX, s.driver.chatmix)
        value = (c.level, c.game, c.chat)
        if value != self._chatmix:
            self._chatmix = value
            self.listener.on_chatmix(*value)

    def _driver_probe(self) -> bool:
        s = self._session
        assert s is not None
        battery = None
        if Cap.BATTERY in s.caps:
            try:
                battery = self._driver_battery(emit=False)
                if battery is None:
                    return False
            except DriverError as exc:
                if exc.code in (ErrorCode.TIMEOUT, ErrorCode.OFFLINE):
                    return False
                log.info("battery unreadable, continuing without it: %s", exc)
        self._online = True
        self._next_battery = time.monotonic() + self.battery_interval
        self._next_chatmix = 0.0
        self.listener.on_device(self._driver_snapshot())
        if battery is not None:
            self.listener.on_battery(battery)
        with self._lock:
            for key, value in self._desired.items():
                self._pending.setdefault(key, value)
        self._process_pending()
        if Cap.SIDETONE_STATUS in s.caps:
            try:
                st = s.call(Cap.SIDETONE_STATUS, s.driver.get_sidetone)
                self.listener.on_sidetone(round(st.current * 100 / 128))
            except DriverError as exc:
                log.info("sidetone unreadable: %s", exc)
        self._set_state(LinkState.CONNECTED)
        return True

    def _driver_snapshot(self) -> DeviceSnapshot:
        s = self._session
        assert s is not None
        drv, dev, caps = s.driver, s.device, s.caps
        eq = drv.EQ
        eq_info = None
        if Cap.EQUALIZER in caps and drv.EQ_FROM_DEVICE:
            try:
                eq = s.call(Cap.EQUALIZER, drv.equalizer_info)
            except DriverError as exc:
                log.info("equalizer layout unreadable: %s", exc)
                eq = None
        if eq is not None and Cap.EQUALIZER in caps:
            freqs = drv.EQ_FREQUENCIES if len(drv.EQ_FREQUENCIES) == eq.bands else default_freqs(eq.bands)
            eq_info = EqualizerInfo(eq.bands, int(max(abs(eq.min), abs(eq.max))), 0, freqs)
            self._eq_layout = EqLayout(tuple(freqs), float(eq.min), float(eq.max), float(eq.step))
        presets: tuple[str, ...] = ()
        if Cap.EQ_PRESET in caps:
            names = [name for name, _values in drv.EQ_PRESETS]
            presets = tuple(names) if len(names) == drv.PRESET_COUNT else tuple(
                f"Preset {i + 1}" for i in range(drv.PRESET_COUNT))
        product = next((e.name for e in dev.entries if e.name), "")
        return DeviceSnapshot(
            name=product or dev.name,
            model=dev.name,
            image=drv.IMAGE or "headset-generic.png",
            hidraw=", ".join(sorted({s.path_for(c) for c in caps})),
            usb_id=dev.usb_id,
            protocol="HeadsetControl",
            firmware=(),
            unit_id="",
            model_id="",
            features=(),
            eq_info=eq_info,
            has_sidetone=Cap.SIDETONE in caps,
            has_equalizer=eq_info is not None,
            has_battery=Cap.BATTERY in caps,
            model_key=f"hc:{type(drv).__name__}",
            verified=False,
            wireless=Cap.BATTERY in caps,
            has_auto_off=Cap.INACTIVE_TIME in caps,
            battery_source="device" if Cap.BATTERY in caps else "none",
            backend="headsetcontrol",
            vendor=dev.vendor,
            controls=tuple(name for name, (cap, _fn, _t) in CONTROLS.items() if cap in caps),
            eq_presets=presets,
            eq_step=float(eq.step) if eq_info is not None and eq is not None else 1.0,
            has_chatmix=Cap.CHATMIX in caps,
        )

    def _driver_apply(self, pending: dict[str, object]) -> None:
        s = self._session
        assert s is not None
        drv, caps = s.driver, s.caps
        if "sidetone" in pending and Cap.SIDETONE in caps:
            ui = max(0, min(100, int(pending["sidetone"])))  # type: ignore[arg-type]
            s.call(Cap.SIDETONE, drv.set_sidetone, round(ui * 128 / 100))
            self.listener.on_sidetone(ui)
        if "equalizer" in pending and Cap.EQUALIZER in caps:
            gains = [float(g) for g in pending["equalizer"]]  # type: ignore[union-attr]
            if self._eq_layout is not None:  # the profile may still be in another headset's layout
                gains = fit_gains(gains, self._eq_layout)
            s.call(Cap.EQUALIZER, drv.set_equalizer, gains)
            self.listener.on_equalizer(gains)
        if "auto_off" in pending and Cap.INACTIVE_TIME in caps:
            minutes = max(0, min(255, int(pending["auto_off"])))  # type: ignore[arg-type]
            self.listener.on_auto_off(s.call(Cap.INACTIVE_TIME, drv.set_inactive_time, minutes))
        for key, value in pending.items():
            if not key.startswith("ctl:") or key[4:] not in CONTROLS:
                continue
            name = key[4:]
            cap, method, kind = CONTROLS[name]
            if cap not in caps:
                continue
            value = kind(value)
            s.call(cap, getattr(drv, method), value)
            self.listener.on_control(name, value)
        if "battery" in pending and Cap.BATTERY in caps:
            if self._driver_battery() is None:
                raise DriverError(ErrorCode.OFFLINE, "battery unavailable")
