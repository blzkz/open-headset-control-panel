"""Qt adapters: HID service → signals, audio manager, microphone level meter."""

from __future__ import annotations

import logging
import math
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from PyQt6.QtCore import QByteArray, QIODevice, QObject, QProcess, QTimer, pyqtSignal

try:  # QtMultimedia links libpulse; without it we only lose the mic level meter
    from PyQt6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
except ImportError:  # pragma: no cover - depends on the host
    QAudioFormat = QAudioSource = QMediaDevices = None

from ..audio import pulse
from ..audio.filterchain import pipewire_available
from ..audio.surround import SURROUND_NODE, SurroundSink, available_rooms
from ..audio.voice import VOICE_NODE, VoiceChain
from ..battery import BatteryState
from ..config import SurroundSettings, VoiceSettings
from ..device import LOGITECH_VID
from ..service import DeviceSnapshot, HeadsetService, LinkState, ServiceListener

log = logging.getLogger(__name__)


class HeadsetBridge(QObject, ServiceListener):
    stateChanged = pyqtSignal(object, str)
    deviceChanged = pyqtSignal(object)
    batteryChanged = pyqtSignal(object)
    sidetoneChanged = pyqtSignal(int)
    equalizerChanged = pyqtSignal(list)
    cableChanged = pyqtSignal(bool)
    autoOffChanged = pyqtSignal(int)
    controlChanged = pyqtSignal(str, object)
    chatmixChanged = pyqtSignal(int, int, int)  # level 0-128, game %, chat %
    errorOccurred = pyqtSignal(str)

    def __init__(self, battery_interval: float, parent: QObject | None = None):
        QObject.__init__(self, parent)
        self.state: LinkState = LinkState.NO_RECEIVER
        self.device: DeviceSnapshot | None = None
        self.battery: BatteryState | None = None
        self.sidetone: int | None = None
        self.gains: list[int] | None = None
        self.cable = False
        self.service = HeadsetService(self, battery_interval=battery_interval)
        # Signals are emitted from the service thread and delivered queued to the GUI.
        self.batteryChanged.connect(lambda b: setattr(self, "battery", b))
        self.sidetoneChanged.connect(lambda v: setattr(self, "sidetone", v))
        self.equalizerChanged.connect(lambda g: setattr(self, "gains", g))
        self.cableChanged.connect(lambda c: setattr(self, "cable", c))
        self.deviceChanged.connect(lambda d: setattr(self, "device", d))
        self.stateChanged.connect(lambda s, _d: setattr(self, "state", s))

    def start(self) -> None:
        self.service.start()

    def stop(self) -> None:
        self.service.stop()
        self.service.join(timeout=2)

    @property
    def connected(self) -> bool:
        return self.state is LinkState.CONNECTED

    # ServiceListener (service thread) ------------------------------------------------
    def on_state(self, state: LinkState, detail: str) -> None:
        self.stateChanged.emit(state, detail)

    def on_device(self, snapshot: DeviceSnapshot) -> None:
        self.deviceChanged.emit(snapshot)

    def on_battery(self, battery: BatteryState) -> None:
        self.batteryChanged.emit(battery)

    def on_sidetone(self, level: int) -> None:
        self.sidetoneChanged.emit(level)

    def on_equalizer(self, gains: list[int]) -> None:
        self.equalizerChanged.emit(list(gains))

    def on_auto_off(self, minutes: int) -> None:
        self.autoOffChanged.emit(minutes)

    def on_control(self, name: str, value: object) -> None:
        self.controlChanged.emit(name, value)

    def on_chatmix(self, level: int, game: int, chat: int) -> None:
        self.chatmixChanged.emit(level, game, chat)

    def on_cable(self, connected: bool) -> None:
        self.cableChanged.emit(connected)

    def on_error(self, message: str) -> None:
        self.errorOccurred.emit(message)


class _SerialWorker:
    """One background thread; per-key latest-wins job queue (keeps the UI fluid)."""

    def __init__(self):
        self._jobs: dict[str, Callable[[], None]] = {}
        self._order: list[str] = []
        self._cv = threading.Condition()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="open-headset-audio", daemon=True)
        self._thread.start()

    def submit(self, key: str, fn: Callable[[], None]) -> None:
        with self._cv:
            if key not in self._jobs:
                self._order.append(key)
            self._jobs[key] = fn
            self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._order and not self._stop:
                    self._cv.wait()
                if self._stop and not self._order:
                    return
                key = self._order.pop(0)
                fn = self._jobs.pop(key)
            try:
                fn()
            except Exception:
                log.exception("audio job %s failed", key)

    def shutdown(self, timeout: float = 3.0) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify()
        self._thread.join(timeout)


class AudioManager(QObject):
    """Headset sink/source control and the PipeWire effects (voice, surround)."""

    stateChanged = pyqtSignal(object)  # pulse.AudioState
    voiceRunning = pyqtSignal(bool)
    surroundRunning = pyqtSignal(bool)
    errorOccurred = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.available = pulse.pactl_available()
        self.pipewire = pipewire_available()
        self.state = pulse.AudioState()
        self.vendor_id, self.product_id = LOGITECH_VID, 0x0ABA
        self.voice = VoiceChain()
        self.surround = SurroundSink()
        self._worker = _SerialWorker()
        self._debounce = QTimer(self, singleShot=True, interval=150)
        self._debounce.timeout.connect(self.refresh)
        self._sub: QProcess | None = None
        self._voice_cfg: VoiceSettings | None = None
        self._surround_cfg: SurroundSettings | None = None
        self._prev_default: dict[str, str] = {}
        self._last_emitted: pulse.AudioState | None = None
        self.stateChanged.connect(self._on_state)
        if self.available:
            self._subscribe()
            self.refresh()

    # -- monitoring ---------------------------------------------------------------------
    def _subscribe(self) -> None:
        self._sub = QProcess(self)
        self._sub.setProgram("pactl")
        self._sub.setArguments(["subscribe"])
        self._sub.readyReadStandardOutput.connect(self._on_events)
        self._sub.finished.connect(lambda *_: self._sub is not None and QTimer.singleShot(3000, self._subscribe))
        self._sub.start()

    def _on_events(self) -> None:
        data = bytes(self._sub.readAllStandardOutput()).decode(errors="replace")
        if pulse.is_relevant_event(data):
            self._debounce.start()

    def set_usb_id(self, vendor: int, product: int) -> None:
        self.vendor_id, self.product_id = vendor, product
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        vid, pid = self.vendor_id, self.product_id

        def job() -> None:
            state = pulse.query(vid, pid)
            # stream start/stop also flips a source between RUNNING/IDLE ("change on
            # source"): nothing the UI shows, so don't wake every page for it
            if force or state != self._last_emitted:
                self._last_emitted = state
                self.stateChanged.emit(state)

        self._worker.submit("refresh", job)

    def _on_state(self, state: pulse.AudioState) -> None:
        old_sink = self.state.sink.name if self.state.sink else None
        old_src = self.state.source.name if self.state.source else None
        self.state = state
        # headset audio (re)appeared or vanished → keep effects attached to it
        if (state.sink.name if state.sink else None) != old_sink and self._surround_cfg is not None:
            self.apply_surround(self._surround_cfg)
        if (state.source.name if state.source else None) != old_src and self._voice_cfg is not None:
            self.apply_voice(self._voice_cfg)

    # -- volume / mute / default -----------------------------------------------------------
    def set_volume(self, kind: str, percent: int) -> None:
        ep = self.state.sink if kind == "sink" else self.state.source
        if ep:
            self._worker.submit(f"vol-{kind}", lambda: pulse.set_volume(kind, ep.name, percent))

    def set_mute(self, kind: str, muted: bool) -> None:
        ep = self.state.sink if kind == "sink" else self.state.source
        if ep:
            self._worker.submit(f"mute-{kind}", lambda: pulse.set_mute(kind, ep.name, muted))

    def set_default(self, kind: str, name: str) -> None:
        self._worker.submit(f"default-{kind}", lambda: pulse.set_default(kind, name))

    # -- effects -------------------------------------------------------------------------
    def apply_voice(self, cfg: VoiceSettings, description: str = "Open Headset · Voice") -> None:
        self._voice_cfg = replace(cfg)
        mic = self.state.source.name if self.state.source else None
        snapshot = self._voice_cfg

        def job() -> None:
            was_running = self.voice.running
            if was_running and not (snapshot.enabled and mic):
                # hand the default back while our node still exists
                self._restore_default("source", VOICE_NODE, mic)
            self.voice.apply(snapshot, mic, description)
            running = self._confirm_started(self.voice, snapshot.enabled, "Voice enhancement")
            if running and snapshot.make_default and not was_running:
                self._take_default("source", VOICE_NODE)
            self.voiceRunning.emit(running)

        self._worker.submit("voice", job)

    def apply_surround(self, cfg: SurroundSettings, description: str = "Open Headset · 7.1 Surround") -> None:
        self._surround_cfg = replace(cfg)
        sink = self.state.sink.name if self.state.sink else None
        snapshot = self._surround_cfg

        def job() -> None:
            was_running = self.surround.running
            if was_running and not (snapshot.enabled and sink):
                # hand the default back while our node still exists
                self._restore_default("sink", SURROUND_NODE, sink)
            self.surround.apply(snapshot, sink, description)
            running = self._confirm_started(self.surround, snapshot.enabled, "Virtual 7.1 surround")
            if running and snapshot.make_default and not was_running:
                self._take_default("sink", SURROUND_NODE)
            self.surroundRunning.emit(running)

        self._worker.submit("surround", job)

    def _confirm_started(self, effect, wanted: bool, name: str) -> bool:
        """A bad graph makes ``pipewire -c`` exit at once: report instead of failing silently."""
        if wanted and effect.running:
            time.sleep(0.4)
            if not effect.running:
                from .i18n import tr

                self.errorOccurred.emit(tr("{name}: PipeWire could not start the filter", name=tr(name)))
        return effect.running

    def _take_default(self, kind: str, node: str) -> None:
        """Make our virtual node the default, remembering what it replaced."""
        previous = pulse.get_default(kind)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if pulse.node_exists(f"{kind}s", node):
                if previous and previous != node:
                    self._prev_default[kind] = previous
                pulse.set_default(kind, node)
                return
            time.sleep(0.1)

    def _restore_default(self, kind: str, node: str, fallback: str | None) -> None:
        """Our node is gone: give the default back to whatever was there before."""
        if pulse.get_default(kind) not in ("", node):
            return  # the user picked something else meanwhile; leave it alone
        previous = self._prev_default.pop(kind, None)
        target = previous if previous and pulse.node_exists(f"{kind}s", previous) else fallback
        if target:
            pulse.set_default(kind, target)

    def test_speaker(self, channel: str) -> None:
        target = SURROUND_NODE if self.surround.running else (self.state.sink.name if self.state.sink else "")
        if target:
            self._worker.submit(f"test-{channel}", lambda: pulse.play_channel_test(target, channel))

    @staticmethod
    def hrtf_rooms() -> list[str]:
        return list(available_rooms())

    def shutdown(self) -> None:
        sub, self._sub = self._sub, None
        if sub is not None:
            sub.blockSignals(True)  # no auto-restart of the subscription
            sub.kill()
            sub.waitForFinished(500)
        self.voice.stop()
        self.surround.stop()
        self._worker.shutdown()


class MicLevelMonitor(QObject):
    """Reads a capture device with QtMultimedia and reports the RMS level in dBFS."""

    level = pyqtSignal(float)
    # True when the device delivers exact zeros: a real microphone always has a
    # noise floor, so this means the headset itself muted the mic (button/boom).
    digitalSilence = pyqtSignal(bool)

    SILENCE_SAMPLES = 48000 * 2

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._source = None
        self._io: QIODevice | None = None
        self._node = ""
        self._zero_run = 0
        self._silent = False

    @property
    def node(self) -> str:
        """Capture device currently metered ("" when stopped)."""
        return self._node

    def start(self, node_name: str) -> bool:
        if self._source is not None and node_name == self._node:
            return True
        self.stop()
        if QMediaDevices is None:
            return False
        device = next((d for d in QMediaDevices.audioInputs() if bytes(d.id()).decode() == node_name), None)
        if device is None:
            return False
        fmt = QAudioFormat()
        fmt.setSampleRate(48000)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._source = QAudioSource(device, fmt, self)
        self._source.setBufferSize(4800)
        self._io = self._source.start()
        if self._io is None:
            self.stop()
            return False
        self._node = node_name
        self._io.readyRead.connect(self._read)
        return True

    def _read(self) -> None:
        if self._io is None:
            return
        data: QByteArray = self._io.readAll()
        raw = bytes(data)
        n = len(raw) // 2
        if n == 0:
            return
        samples = struct.unpack(f"<{n}h", raw[: n * 2])
        if any(samples):
            self._zero_run = 0
            if self._silent:
                self._silent = False
                self.digitalSilence.emit(False)
        else:
            self._zero_run += n
            if not self._silent and self._zero_run >= self.SILENCE_SAMPLES:
                self._silent = True
                self.digitalSilence.emit(True)
        rms = math.sqrt(sum(s * s for s in samples) / n) / 32768.0
        self.level.emit(20 * math.log10(rms) if rms > 1e-6 else -90.0)

    def stop(self) -> None:
        if self._source is not None:
            self._source.stop()
            self._source.deleteLater()
        self._source = None
        self._io = None
        self._node = ""
        self._zero_run = 0
        if self._silent:
            self._silent = False
            self.digitalSilence.emit(False)
