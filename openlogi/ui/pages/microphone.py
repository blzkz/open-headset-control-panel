"""Microphone page: sidetone, capture volume, level meter and voice enhancement."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ...audio.voice import VOICE_PRESETS
from ...service import LinkState
from ..bridge import MicLevelMonitor
from ..controller import Controller
from ..i18n import tr
from ..widgets.common import Card, SwitchRow, ToggleSwitch, ValueSlider, hline, label
from ..widgets.visuals import LevelMeter

VOICE_PRESET_NAMES = {
    "broadcast": "Broadcast",
    "natural": "Natural",
    "clear": "Clear & crisp",
    "warm": "Warm",
    "streamer": "Streamer",
    "custom": "Custom",
}


def _db(v: int) -> str:
    return f"{v:+d} dB" if v else "0 dB"


class MicrophonePage(QWidget):
    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        self.monitor = MicLevelMonitor(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        grid = QGridLayout(body)
        grid.setContentsMargins(36, 28, 36, 28)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(20)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        # -- sidetone ------------------------------------------------------------------
        st = Card(tr("Sidetone"))
        st.body.addWidget(label(tr("Hear your own voice in the headset"), "Faint"))
        self.sidetone = ValueSlider(tr("Level"), 0, 100, ctl.profile.sidetone, fmt=lambda v: f"{v}%")
        self.sidetone.valueChanged.connect(ctl.set_sidetone)
        st.body.addWidget(self.sidetone)
        grid.addWidget(st, 0, 0)

        # -- capture device -----------------------------------------------------------------
        mic = Card(tr("Microphone"))
        self.mic_volume = ValueSlider(tr("Mic volume"), 0, 100, 100, fmt=lambda v: f"{v}%")
        self.mic_volume.valueChanged.connect(lambda v: ctl.audio.set_volume("source", v))
        mic.body.addWidget(self.mic_volume)
        self.mic_mute = SwitchRow(tr("Mute microphone"))
        self.mic_mute.toggled.connect(lambda on: ctl.audio.set_mute("source", on))
        mic.body.addWidget(self.mic_mute)
        mic.body.addWidget(hline())
        lv = QHBoxLayout()
        lv.addWidget(label(tr("Input level"), "Muted"))
        lv.addStretch(1)
        mic.body.addLayout(lv)
        self.meter = LevelMeter()
        self.monitor.level.connect(self.meter.set_level)
        mic.body.addWidget(self.meter)
        self.hw_muted = label(tr("The headset is sending digital silence: check its mic mute button "
                                 "and that the boom microphone is fully plugged in."), "Banner", wrap=True)
        self.hw_muted.hide()
        self.monitor.digitalSilence.connect(self.hw_muted.setVisible)
        mic.body.addWidget(self.hw_muted)
        self.mic_missing = label(tr("Microphone not available"), "Banner")
        self.mic_missing.hide()
        mic.body.addWidget(self.mic_missing)
        grid.addWidget(mic, 1, 0)

        # -- voice enhancement ------------------------------------------------------------------
        v = ctl.profile.voice
        self.voice_switch = ToggleSwitch(checked=v.enabled)
        voice = Card(tr("Voice enhancement"), trailing=self.voice_switch)
        voice.body.addWidget(label(tr("Software voice processing (Blue VO!CE style) creating a new virtual microphone"),
                                   "Faint", wrap=True))
        self.voice_switch.toggled.connect(lambda on: ctl.update_voice(enabled=on))

        self.voice_controls = QWidget()
        vc = QVBoxLayout(self.voice_controls)
        vc.setContentsMargins(0, 4, 0, 0)
        vc.setSpacing(16)

        prow = QHBoxLayout()
        prow.addWidget(label(tr("Voice preset"), "Muted"))
        prow.addStretch(1)
        self.voice_preset = QComboBox()
        self.voice_preset.setMinimumWidth(180)
        for key in (*VOICE_PRESETS, "custom"):
            self.voice_preset.addItem(tr(VOICE_PRESET_NAMES[key]), key)
        self.voice_preset.activated.connect(self._preset_chosen)
        prow.addWidget(self.voice_preset)
        vc.addLayout(prow)

        self.ns = SwitchRow(tr("Noise reduction (WebRTC)"))
        self.ns.toggled.connect(lambda on: ctl.update_voice(noise_suppression=on))
        vc.addWidget(self.ns)

        self.hp = SwitchRow(tr("High-pass filter"))
        self.hp.toggled.connect(lambda on: ctl.update_voice(highpass_enabled=on))
        vc.addWidget(self.hp)
        self.hp_freq = ValueSlider("", 40, 250, v.highpass_hz, fmt=lambda x: f"{x} Hz", step=5)
        self.hp_freq.released.connect(lambda x: ctl.update_voice(highpass_hz=x))
        vc.addWidget(self.hp_freq)

        self.gate = SwitchRow(tr("Noise gate"))
        self.gate.toggled.connect(lambda on: ctl.update_voice(gate_enabled=on))
        vc.addWidget(self.gate)
        self.gate_thr = ValueSlider(tr("Threshold"), -80, -20, int(v.gate_threshold_db), fmt=lambda x: f"{x} dB")
        self.gate_thr.released.connect(lambda x: ctl.update_voice(gate_threshold_db=float(x)))
        vc.addWidget(self.gate_thr)
        vc.addWidget(hline())

        self.eq_sliders: dict[str, ValueSlider] = {}
        for key, title in (("bass_db", "Bass"), ("presence_db", "Presence"),
                           ("deesser_db", "De-esser"), ("air_db", "Air")):
            s = ValueSlider(tr(title), -12, 12, int(getattr(v, key)), fmt=_db)
            s.released.connect(lambda x, k=key: ctl.update_voice(**{k: float(x)}))
            self.eq_sliders[key] = s
            vc.addWidget(s)
        self.gain = ValueSlider(tr("Output gain"), -12, 18, int(v.gain_db), fmt=_db)
        self.gain.released.connect(lambda x: ctl.update_voice(gain_db=float(x)))
        vc.addWidget(self.gain)
        vc.addWidget(hline())
        self.voice_default = SwitchRow(tr("Use as default microphone"))
        self.voice_default.toggled.connect(lambda on: ctl.update_voice(make_default=on))
        vc.addWidget(self.voice_default)
        voice.body.addWidget(self.voice_controls)
        if not ctl.audio.pipewire:
            voice.body.addWidget(label(tr("PipeWire is required for this feature"), "Banner"))
            self.voice_switch.setEnabled(False)
        grid.addWidget(voice, 0, 1, 3, 1)
        grid.setRowStretch(2, 1)

        ctl.sidetoneChanged.connect(self.sidetone.setValueSilently)
        ctl.voiceChanged.connect(self._sync_voice)
        ctl.audio.stateChanged.connect(self._on_audio)
        ctl.bridge.stateChanged.connect(self._on_link)
        self._sync_voice()
        self._on_audio(ctl.audio.state)
        self._on_link(ctl.bridge.state, "")

    # -- sync ------------------------------------------------------------------------------
    def _on_link(self, state: LinkState, _d: str) -> None:
        dev = self.ctl.bridge.device
        self.sidetone.setEnabled(dev is None or dev.has_sidetone)

    def _on_audio(self, st) -> None:
        has = st.source is not None
        self.mic_missing.setVisible(not has)
        self.mic_volume.setEnabled(has)
        self.mic_mute.setEnabled(has)
        if has:
            self.mic_volume.setValueSilently(st.source.volume)
            self.mic_mute.setCheckedSilently(st.source.muted)
        # Re-open the meter only when the headset mic itself changed: restarting it on
        # every audio event re-triggers those events (and KDE's "mic in use" indicator).
        node = st.source.name if has else ""
        if self.isVisible() and node != self.monitor.node:
            self._restart_meter()

    def _sync_voice(self) -> None:
        v = self.ctl.profile.voice
        self.voice_switch.setCheckedSilently(v.enabled)
        self.voice_controls.setEnabled(v.enabled)
        idx = self.voice_preset.findData(v.preset)
        self.voice_preset.setCurrentIndex(idx if idx >= 0 else self.voice_preset.count() - 1)
        self.ns.setCheckedSilently(v.noise_suppression)
        self.hp.setCheckedSilently(v.highpass_enabled)
        self.hp_freq.setValueSilently(v.highpass_hz)
        self.hp_freq.setEnabled(v.highpass_enabled)
        self.gate.setCheckedSilently(v.gate_enabled)
        self.gate_thr.setValueSilently(int(v.gate_threshold_db))
        self.gate_thr.setEnabled(v.gate_enabled)
        for key, s in self.eq_sliders.items():
            s.setValueSilently(int(getattr(v, key)))
        self.gain.setValueSilently(int(v.gain_db))
        self.voice_default.setCheckedSilently(v.make_default)

    def _preset_chosen(self, index: int) -> None:
        key = self.voice_preset.itemData(index)
        if key and key != "custom":
            self.ctl.update_voice(preset=key)

    # -- meter only while visible (keeps the mic closed otherwise) --------------------------------
    def _restart_meter(self, attempt: int = 0) -> None:
        if not self.isVisible():
            return
        # meter (and hardware-mute detection) on the raw headset mic, before any processing
        st = self.ctl.audio.state
        node = st.source.name if st.source else ""
        self.monitor.stop()
        self.meter.reset()
        # a freshly created virtual source takes a moment to reach Qt's device list
        if node and not self.monitor.start(node) and attempt < 4:
            QTimer.singleShot(600, lambda: self._restart_meter(attempt + 1))

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._restart_meter()

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self.monitor.stop()
        self.meter.reset()

