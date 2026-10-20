"""Audio page: headset output volume/default + virtual 7.1 surround."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ...audio.surround import SURROUND_NODE, available_rooms
from .. import theme
from ..controller import Controller
from ..i18n import tr
from ..icons import icon
from ..widgets.common import Card, SwitchRow, ToggleSwitch, ValueSlider, hline, label
from ..widgets.visuals import SpeakerMap

ROOM_NAMES = {"kemar": "MIT KEMAR", "default": "libmysofa default"}


class SurroundPage(QWidget):
    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
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
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)

        # -- output ------------------------------------------------------------------------
        out = Card(tr("Headset volume"))
        self.volume = ValueSlider(tr("Level"), 0, 100, 50, fmt=lambda v: f"{v}%")
        self.volume.valueChanged.connect(lambda v: ctl.audio.set_volume("sink", v))
        out.body.addWidget(self.volume)
        self.mute = SwitchRow(tr("Mute"))
        self.mute.toggled.connect(lambda on: ctl.audio.set_mute("sink", on))
        out.body.addWidget(self.mute)
        out.body.addWidget(hline())
        self.default_lbl = label("", "Faint", wrap=True)
        out.body.addWidget(self.default_lbl)
        row = QHBoxLayout()
        row.addStretch(1)
        self.default_btn = QPushButton(icon("check", theme.TEXT2), "  " + tr("Set as default output"))
        self.default_btn.setObjectName("Ghost")
        self.default_btn.clicked.connect(self._make_default)
        row.addWidget(self.default_btn)
        out.body.addLayout(row)
        self.missing = label(tr("Headset audio device not found"), "Banner")
        self.missing.hide()
        out.body.addWidget(self.missing)
        grid.addWidget(out, 0, 0)

        # -- surround settings -------------------------------------------------------------------
        s = ctl.profile.surround
        self.sw = ToggleSwitch(checked=s.enabled)
        sur = Card(tr("Virtual 7.1 surround"), trailing=self.sw)
        sur.body.addWidget(label(tr("HRTF binaural rendering of 7.1 content (DTS Headphone:X style), as a new audio output"),
                                 "Faint", wrap=True))
        self.sw.toggled.connect(lambda on: ctl.update_surround(enabled=on))
        rrow = QHBoxLayout()
        rrow.addWidget(label(tr("HRTF dataset"), "Muted"))
        rrow.addStretch(1)
        self.room = QComboBox()
        self.room.setMinimumWidth(170)
        rooms = available_rooms()
        for key in rooms:
            self.room.addItem(ROOM_NAMES.get(key, key), key)
        self.room.activated.connect(lambda i: ctl.update_surround(room=self.room.itemData(i)))
        rrow.addWidget(self.room)
        sur.body.addLayout(rrow)
        self.gain = ValueSlider(tr("Level"), -12, 6, int(s.gain_db), fmt=lambda v: f"{v:+d} dB" if v else "0 dB")
        self.gain.released.connect(lambda v: ctl.update_surround(gain_db=float(v)))
        sur.body.addWidget(self.gain)
        self.sur_default = SwitchRow(tr("Use as default output"))
        self.sur_default.toggled.connect(lambda on: ctl.update_surround(make_default=on))
        sur.body.addWidget(self.sur_default)
        if not ctl.audio.pipewire:
            sur.body.addWidget(label(tr("PipeWire is required for this feature"), "Banner"))
            self.sw.setEnabled(False)
        elif not rooms:
            sur.body.addWidget(label(tr("No SOFA HRTF dataset found (install libmysofa)"), "Banner"))
            self.sw.setEnabled(False)
        grid.addWidget(sur, 1, 0)
        grid.setRowStretch(2, 1)

        # -- speaker map ---------------------------------------------------------------------
        mapcard = Card(tr("Surround"))
        self.map = SpeakerMap()
        self.map.speakerClicked.connect(self._test)
        mapcard.body.addWidget(self.map, 1)
        hint = label(tr("Click a speaker to test it"), "Faint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mapcard.body.addWidget(hint)
        grid.addWidget(mapcard, 0, 1, 3, 1)

        ctl.audio.stateChanged.connect(self._on_audio)
        ctl.surroundChanged.connect(self._sync)
        ctl.audio.surroundRunning.connect(lambda _r: self._on_audio(ctl.audio.state))
        self._sync()
        self._on_audio(ctl.audio.state)

    def _test(self, ch: str) -> None:
        self.map.flash(ch)
        self.ctl.audio.test_speaker(ch)

    def _make_default(self) -> None:
        st = self.ctl.audio.state
        target = SURROUND_NODE if self.ctl.audio.surround.running else (st.sink.name if st.sink else "")
        if target:
            self.ctl.audio.set_default("sink", target)

    def _sync(self) -> None:
        s = self.ctl.profile.surround
        self.sw.setCheckedSilently(s.enabled)
        i = self.room.findData(s.room)
        if i >= 0:
            self.room.setCurrentIndex(i)
        self.gain.setValueSilently(int(s.gain_db))
        self.sur_default.setCheckedSilently(s.make_default)
        for w in (self.room, self.gain, self.sur_default):
            w.setEnabled(s.enabled)

    def _on_audio(self, st) -> None:
        has = st.sink is not None
        self.missing.setVisible(not has)
        for w in (self.volume, self.mute, self.default_btn, self.map):
            w.setEnabled(has)
        if not has:
            self.default_lbl.setText("")
            return
        self.volume.setValueSilently(st.sink.volume)
        self.mute.setCheckedSilently(st.sink.muted)
        is_ours = st.default_sink in (st.sink.name, SURROUND_NODE)
        current = st.descriptions.get(st.default_sink, st.default_sink or "—")
        self.default_lbl.setText(f"{tr('Default output')}: {current}")
        self.default_lbl.setStyleSheet(f"color: {theme.GOOD if is_ours else theme.TEXT3}; font-size: 12px;")
        self.default_btn.setVisible(not is_ours)
