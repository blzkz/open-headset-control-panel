"""Headset page: the extra controls of headsets driven by HeadsetControl ports.

Only what the connected model supports is shown (DeviceSnapshot.controls): onboard
presets, game/chat balance, microphone options, prompts, limiter, Bluetooth, lights.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...service import LinkState
from .. import theme
from ..controller import Controller
from ..i18n import tr
from ..widgets.common import Card, SwitchRow, ValueSlider, label

SWITCHES = (
    ("voice_prompts", "Voice prompts", "Spoken announcements and tones from the headset"),
    ("lights", "Lights", "LEDs on the headset"),
    ("volume_limiter", "Volume limiter", "Caps the maximum output level to protect your hearing"),
    ("rotate_to_mute", "Rotate to mute", "Mutes the microphone when the boom is raised"),
    ("bt_when_powered_on", "Bluetooth at power-on", "Turn Bluetooth on together with the headset"),
)
NOISE_LEVELS = ("Off", "Low", "High")
LED_LEVELS = ("Off", "Low", "Medium", "High")
BT_CALL = ("Keep game audio", "Lower game audio by 12 dB", "Mute game audio")


class ChatmixBar(QWidget):
    """Game ↔ chat balance as reported by the headset's mix dial."""

    def __init__(self):
        super().__init__()
        self.level = 64
        self.setMinimumHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_level(self, level: int) -> None:
        self.level = max(0, min(128, level))
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(8, 11, -8, -11)
        grad = QLinearGradient(r.topLeft(), r.topRight())
        grad.setColorAt(0, QColor(theme.ACCENT))
        grad.setColorAt(1, QColor("#bf5af2"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        x = r.left() + r.width() * self.level / 128
        p.setPen(QPen(QColor(theme.BG2), 3))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x - 9, r.center().y() - 9, 18, 18))


def _combo(items: tuple[str, ...]) -> QComboBox:
    box = QComboBox()
    box.setMinimumWidth(190)
    for i, text in enumerate(items):
        box.addItem(tr(text), i)
    return box


def _row(title: str, description: str, widget: QWidget) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(16)
    text = QVBoxLayout()
    text.setSpacing(3)
    text.addWidget(label(tr(title)))
    if description:
        text.addWidget(label(tr(description), "Faint", wrap=True))
    lay.addLayout(text, 1)
    lay.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
    return w


class ControlsPage(QWidget):
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
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        # -- onboard presets ------------------------------------------------------------
        self.presets_card = Card(tr("Onboard presets"))
        self.presets_card.body.addWidget(
            label(tr("Equalizer presets stored in the headset itself"), "Faint", wrap=True))
        self.preset_box = QHBoxLayout()
        self.preset_box.setSpacing(8)
        self.presets_card.body.addLayout(self.preset_box)
        self.preset_group = QButtonGroup(self)
        self.preset_group.setExclusive(True)
        grid.addWidget(self.presets_card, 0, 0, 1, 2)

        # -- chat mix ---------------------------------------------------------------------
        self.mix_card = Card(tr("Game / chat mix"))
        self.mix_card.body.addWidget(label(tr("Set with the dial on the headset"), "Faint", wrap=True))
        self.mix_bar = ChatmixBar()
        self.mix_card.body.addWidget(self.mix_bar)
        mix_labels = QHBoxLayout()
        self.game_lbl = label("", "Value")
        self.chat_lbl = label("", "Value")
        mix_labels.addWidget(self.game_lbl)
        mix_labels.addStretch(1)
        mix_labels.addWidget(self.chat_lbl)
        self.mix_card.body.addLayout(mix_labels)
        grid.addWidget(self.mix_card, 1, 0, 1, 2)

        # -- microphone ---------------------------------------------------------------------
        self.mic_card = Card(tr("Microphone"))
        self.mic_volume = ValueSlider(tr("Microphone volume"), 0, 100, 100, fmt=lambda v: f"{v}%")
        self.mic_volume.released.connect(lambda v: self.ctl.set_control("mic_volume", round(v * 128 / 100)))
        self.mic_card.body.addWidget(self.mic_volume)
        self.noise = _combo(NOISE_LEVELS)
        self.noise.activated.connect(lambda i: self.ctl.set_control("noise_filter", i))
        self.noise_row = _row("Noise filter", "Filters background noise in the headset", self.noise)
        self.mic_card.body.addWidget(self.noise_row)
        self.led = _combo(LED_LEVELS)
        self.led.activated.connect(lambda i: self.ctl.set_control("mic_mute_led", i))
        self.led_row = _row("Mute LED", "Brightness of the LED that shows the microphone is muted", self.led)
        self.mic_card.body.addWidget(self.led_row)
        grid.addWidget(self.mic_card, 2, 0)

        # -- headset options --------------------------------------------------------------------
        self.opts_card = Card(tr("Headset"))
        self.switches: dict[str, SwitchRow] = {}
        for key, title, desc in SWITCHES:
            row = SwitchRow(tr(title), tr(desc))
            row.toggled.connect(lambda on, k=key: self.ctl.set_control(k, on))
            self.opts_card.body.addWidget(row)
            self.switches[key] = row
        self.bt_call = _combo(BT_CALL)
        self.bt_call.activated.connect(lambda i: self.ctl.set_control("bt_call_volume", i))
        self.bt_call_row = _row("Bluetooth calls", "What happens to game audio during a phone call", self.bt_call)
        self.opts_card.body.addWidget(self.bt_call_row)
        sounds = QHBoxLayout()
        sounds.setSpacing(8)
        for i in range(2):
            b = QPushButton(tr("Sound {n}", n=i + 1))
            b.setObjectName("Ghost")
            b.clicked.connect(lambda _=False, n=i: self.ctl.set_control("notification_sound", n))
            sounds.addWidget(b)
        holder = QWidget()
        holder.setLayout(sounds)
        self.sound_row = _row("Notification sound", "Play a sound on the headset", holder)
        self.opts_card.body.addWidget(self.sound_row)
        grid.addWidget(self.opts_card, 2, 1)
        grid.setRowStretch(3, 1)

        self.offline = label(tr("Turn the headset on to change these settings"), "Banner")
        grid.addWidget(self.offline, 3, 0, 1, 2, Qt.AlignmentFlag.AlignTop)

        b = ctl.bridge
        b.deviceChanged.connect(self._on_device)
        b.stateChanged.connect(self._on_state)
        b.chatmixChanged.connect(self._on_chatmix)
        ctl.controlChanged.connect(self._on_control)
        ctl.profileChanged.connect(self._from_profile)
        if b.device:
            self._on_device(b.device)
        self._on_state(b.state, "")
        self._on_chatmix(64, 100, 100)

    # -- updates --------------------------------------------------------------------------------
    def _on_device(self, d) -> None:
        controls = set(d.controls)
        for btn in self.preset_group.buttons():
            self.preset_group.removeButton(btn)
            btn.deleteLater()
        for i, name in enumerate(d.eq_presets):
            numbered = name.startswith("Preset ") and name[7:].isdigit()
            btn = QPushButton(tr("Preset {n}", n=name[7:]) if numbered else tr(name))
            btn.setObjectName("Preset")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, n=i: self.ctl.set_control("eq_preset", n))
            self.preset_group.addButton(btn, i)
            self.preset_box.addWidget(btn)
        self.presets_card.setVisible(bool(d.eq_presets))
        self.mix_card.setVisible(d.has_chatmix)
        self.mic_volume.setVisible("mic_volume" in controls)
        self.noise_row.setVisible("noise_filter" in controls)
        self.led_row.setVisible("mic_mute_led" in controls)
        self.mic_card.setVisible(bool(controls & {"mic_volume", "noise_filter", "mic_mute_led"}))
        for key, row in self.switches.items():
            row.setVisible(key in controls)
        self.bt_call_row.setVisible("bt_call_volume" in controls)
        self.sound_row.setVisible("notification_sound" in controls)
        self.opts_card.setVisible(bool(controls & ({k for k, _t, _d in SWITCHES}
                                                   | {"bt_call_volume", "notification_sound"})))
        self._from_profile()

    def _from_profile(self) -> None:
        for name, value in self.ctl.profile.controls.items():
            self._on_control(name, value)

    def _on_control(self, name: str, value: object) -> None:
        if name in self.switches:
            self.switches[name].setCheckedSilently(bool(value))
        elif name == "mic_volume":
            self.mic_volume.setValueSilently(round(int(value) * 100 / 128))  # type: ignore[call-overload]
        elif name == "noise_filter":
            self.noise.setCurrentIndex(int(value))  # type: ignore[call-overload]
        elif name == "mic_mute_led":
            self.led.setCurrentIndex(int(value))  # type: ignore[call-overload]
        elif name == "bt_call_volume":
            self.bt_call.setCurrentIndex(int(value))  # type: ignore[call-overload]
        elif name == "eq_preset":
            btn = self.preset_group.button(int(value))  # type: ignore[call-overload]
            if btn:
                btn.setChecked(True)

    def _on_chatmix(self, level: int, game: int, chat: int) -> None:
        self.mix_bar.set_level(level)
        self.game_lbl.setText(tr("Game {p}%", p=game))
        self.chat_lbl.setText(tr("Chat {p}%", p=chat))

    def _on_state(self, state: LinkState, _detail: str) -> None:
        online = state is LinkState.CONNECTED
        self.offline.setVisible(not online)
        for card in (self.presets_card, self.mic_card, self.opts_card):
            card.setEnabled(online)
