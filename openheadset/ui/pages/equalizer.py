"""Equalizer page: preset list on the left, interactive curve on the right."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...presets import BUILTIN_PRESETS, CUSTOM_KEY
from ...service import LinkState
from .. import theme
from ..controller import Controller
from ..i18n import tr
from ..icons import icon
from ..widgets.common import label
from ..widgets.eqgraph import EqualizerGraph


class EqualizerPage(QWidget):
    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- preset panel -------------------------------------------------------------
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setFixedWidth(250)
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(0, 24, 0, 16)
        pv.setSpacing(0)
        head = label(tr("Presets").upper(), "H3")
        head.setContentsMargins(20, 0, 0, 10)
        pv.addWidget(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self.list_layout = QVBoxLayout(inner)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        scroll.setWidget(inner)
        pv.addWidget(scroll, 1)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for preset in BUILTIN_PRESETS:
            self._add_button(preset.key, tr(preset.name))
        self.custom_btn = self._add_button(CUSTOM_KEY, tr("Custom"))
        self.user_head = label(tr("My presets").upper(), "H3")
        self.user_head.setContentsMargins(20, 18, 0, 10)
        self.list_layout.addWidget(self.user_head)
        self.user_box = QVBoxLayout()
        self.user_box.setSpacing(0)
        self.list_layout.addLayout(self.user_box)
        self.list_layout.addStretch(1)

        actions = QVBoxLayout()
        actions.setContentsMargins(16, 10, 16, 0)
        actions.setSpacing(8)
        self.save_btn = QPushButton(icon("save", theme.TEXT2), "  " + tr("Save as preset…"))
        self.save_btn.setObjectName("Ghost")
        self.save_btn.clicked.connect(self._save_preset)
        self.del_btn = QPushButton(icon("trash", theme.TEXT2), "  " + tr("Delete preset"))
        self.del_btn.setObjectName("Ghost")
        self.del_btn.clicked.connect(self._delete_preset)
        actions.addWidget(self.save_btn)
        actions.addWidget(self.del_btn)
        pv.addLayout(actions)
        root.addWidget(panel)

        # -- graph area ------------------------------------------------------------------
        area = QVBoxLayout()
        area.setContentsMargins(36, 28, 36, 24)
        area.setSpacing(6)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        titles.addWidget(label(tr("Equalizer").upper(), "H2"))
        self.subtitle = label(tr("Onboard 10-band equalizer · stored in the headset"), "Faint")
        titles.addWidget(self.subtitle)
        top.addLayout(titles)
        top.addStretch(1)
        self.preset_name = label("", "Value")
        self.preset_name.setStyleSheet(f"color: {theme.ACCENT}; font-size: 15px; font-weight: 600;")
        top.addWidget(self.preset_name)
        top.addSpacing(16)
        reset = QPushButton(icon("reset", theme.TEXT2), "  " + tr("Reset"))
        reset.setObjectName("Ghost")
        reset.clicked.connect(lambda: self.ctl.apply_eq_preset("flat"))
        top.addWidget(reset)
        area.addLayout(top)
        area.addSpacing(10)

        self.no_eq = label(tr("This model has no onboard equalizer"), "Banner")
        self.no_eq.hide()
        area.addWidget(self.no_eq)

        self.graph = EqualizerGraph()
        self.graph.gainsChanged.connect(lambda g: self.ctl.set_eq(g))
        self.graph.gainsCommitted.connect(lambda g: self.ctl.set_eq(g))
        area.addWidget(self.graph, 1)
        hint = label(tr("Drag the points, use the mouse wheel or arrow keys. Double-click resets a band."), "Faint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        area.addWidget(hint)
        root.addLayout(area, 1)

        ctl.eqChanged.connect(self._on_eq)
        ctl.userPresetsChanged.connect(self._rebuild_user)
        ctl.bridge.deviceChanged.connect(self._on_device)
        ctl.bridge.stateChanged.connect(self._on_state)
        self._rebuild_user()
        self._on_eq(ctl.profile.eq_gains, ctl.profile.eq_preset, animate=False)
        self._on_state(ctl.bridge.state, "")

    def _add_button(self, key: str, text: str, box: QVBoxLayout | None = None) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("Preset")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if key != CUSTOM_KEY:
            btn.clicked.connect(lambda _=False, k=key: self.ctl.apply_eq_preset(k))
        else:
            btn.setEnabled(False)
        self.group.addButton(btn)
        self.buttons[key] = btn
        (box or self.list_layout).addWidget(btn)
        return btn

    def _rebuild_user(self) -> None:
        for key in [k for k in self.buttons if k.startswith("user:")]:
            btn = self.buttons.pop(key)
            self.group.removeButton(btn)
            btn.deleteLater()
        for name in sorted(self.ctl.config.user_eq_presets):
            self._add_button(f"user:{name}", name, self.user_box)
        self.user_head.setVisible(bool(self.ctl.config.user_eq_presets))
        self._sync_selection(self.ctl.profile.eq_preset)

    def _sync_selection(self, key: str) -> None:
        btn = self.buttons.get(key, self.custom_btn)
        btn.setChecked(True)
        self.custom_btn.setVisible(key == CUSTOM_KEY)
        self.del_btn.setVisible(key.startswith("user:"))
        self.preset_name.setText(btn.text())

    def _on_eq(self, gains: list[float], key: str, animate: bool = True) -> None:
        self.graph.set_gains(gains, animate=animate)
        self._sync_selection(key)

    def _on_device(self, snap) -> None:
        self._on_state(self.ctl.bridge.state, "")
        if snap.eq_info:
            self.graph.set_bands(snap.eq_info.frequencies, snap.eq_info.db_range, snap.eq_step)
            self.graph.set_gains(self.ctl.profile.eq_gains, animate=False)
            n = snap.eq_info.band_count
            where = tr("stored in the headset")
            self.subtitle.setText(tr("Onboard {n}-band equalizer", n=n) + " · " + where)

    def _on_state(self, state: LinkState, _detail: str) -> None:
        # Editing stays possible offline: the profile is applied when the headset connects.
        dev = self.ctl.bridge.device
        self.graph.setEnabled(dev is None or dev.has_equalizer)
        self.no_eq.setVisible(dev is not None and not dev.has_equalizer)
        self.preset_name.setToolTip("" if state is LinkState.CONNECTED else tr("Headset off"))

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, tr("Save as preset…"), tr("Preset name"))
        name = name.strip()
        if ok and name:
            self.ctl.save_user_preset(name)

    def _delete_preset(self) -> None:
        key = self.ctl.profile.eq_preset
        if key.startswith("user:"):
            self.ctl.delete_user_preset(key[5:])
