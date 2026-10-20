"""Application controller: profiles/config ↔ headset service ↔ audio effects."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..audio.voice import apply_preset as apply_voice_preset
from ..audio.voice import matches_preset as voice_matches_preset
from ..config import Config, Profile
from ..presets import PRESETS_BY_KEY, EqLayout, fit_gains, match_preset
from ..service import ACTIONS, LinkState
from .bridge import AudioManager, HeadsetBridge
from .updates import UpdateManager
from .i18n import tr


class Controller(QObject):
    profileChanged = pyqtSignal()        # active profile switched / profile list changed
    eqChanged = pyqtSignal(list, str)    # gains, preset key
    sidetoneChanged = pyqtSignal(int)
    voiceChanged = pyqtSignal()
    surroundChanged = pyqtSignal()
    userPresetsChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    lightingChanged = pyqtSignal()
    autoOffChanged = pyqtSignal(int)
    controlChanged = pyqtSignal(str, object)  # headset control name, value

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(parent)
        self.config = config
        self.bridge = HeadsetBridge(battery_interval=config.app.battery_poll_seconds, parent=self)
        self.audio = AudioManager(self)
        self.updates = UpdateManager(config.app.auto_check_updates, self)
        self._save_timer = QTimer(self, singleShot=True, interval=600)
        self._save_timer.timeout.connect(lambda: self.config.save())

        self.bridge.sidetoneChanged.connect(self._device_sidetone)
        self.bridge.equalizerChanged.connect(self._device_eq)
        self.bridge.deviceChanged.connect(self._device_info)
        self.bridge.autoOffChanged.connect(self.autoOffChanged)
        self.bridge.controlChanged.connect(self._device_control)
        self.layout = EqLayout()
        self._push_desired()

    # -- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        self.bridge.start()
        self._apply_effects()

    def shutdown(self) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
        self.config.save()
        self.bridge.stop()
        self.audio.shutdown()

    @property
    def profile(self) -> Profile:
        return self.config.profile

    def schedule_save(self) -> None:
        self._save_timer.start()

    def _lights_payload(self) -> dict[int, tuple[int, tuple[int, int, int], int]]:
        light = self.profile.lighting
        mode = {"off": 0, "static": 1, "breathing": 2}.get(light.mode, 1)
        # the service only sends zones the connected model actually has
        return {zone: (mode, light.rgb(), light.brightness) for zone in (0, 1)}

    def _push_desired(self) -> None:
        prof = self.profile
        if self.config.app.apply_on_connect:
            self.bridge.service.set_desired_state(prof.sidetone, prof.eq_gains,
                                                  self.config.auto_off_minutes, self._lights_payload(),
                                                  prof.controls)
        else:
            self.bridge.service.set_desired_state(None, None)

    def _apply_effects(self) -> None:
        self.audio.apply_voice(self.profile.voice, "Open Headset · " + tr("Voice enhancement"))
        self.audio.apply_surround(self.profile.surround, "Open Headset · " + tr("Virtual 7.1 surround"))

    # -- device feedback ------------------------------------------------------------------
    def _device_info(self, snap) -> None:
        try:
            vid, pid = (int(x, 16) for x in snap.usb_id.split(":"))
            self.audio.set_usb_id(vid, pid)
        except ValueError:
            pass
        if snap.eq_info:
            info = snap.eq_info
            layout = EqLayout(tuple(info.frequencies), -float(info.db_range), float(info.db_range), snap.eq_step)
            if layout != self.layout:
                self.layout = layout
                self._fit_profiles()

    def _fit_profiles(self) -> None:
        """Express every profile's EQ in the connected headset's band layout."""
        for prof in self.config.profiles.values():
            gains = fit_gains(prof.eq_gains, self.layout, tuple(prof.eq_freqs))
            if gains != prof.eq_gains or list(self.layout.freqs) != prof.eq_freqs:
                prof.eq_gains, prof.eq_freqs = gains, list(self.layout.freqs)
                prof.eq_preset = match_preset(gains, self.config.user_eq_presets, self.layout)
        self._push_desired()
        self.eqChanged.emit(list(self.profile.eq_gains), self.profile.eq_preset)
        self.schedule_save()

    def _device_control(self, name: str, value: object) -> None:
        if name not in ACTIONS and self.profile.controls.get(name) != value:
            self.profile.controls[name] = value
            self._push_desired()
            self.schedule_save()
        self.controlChanged.emit(name, value)

    def _device_sidetone(self, level: int) -> None:
        if self.profile.sidetone != level:
            self.profile.sidetone = level
            self._push_desired()
            self.schedule_save()
        self.sidetoneChanged.emit(level)

    def _device_eq(self, gains: list[float]) -> None:
        prof = self.profile
        gains = [self.layout.snap(g) for g in gains]
        if prof.eq_gains != gains:
            prof.eq_gains = gains
            prof.eq_preset = match_preset(gains, self.config.user_eq_presets, self.layout)
            self._push_desired()
            self.schedule_save()
        self.eqChanged.emit(list(prof.eq_gains), prof.eq_preset)

    # -- sidetone / EQ ----------------------------------------------------------------------
    def set_sidetone(self, level: int) -> None:
        self.profile.sidetone = int(level)
        self._push_desired()
        self.bridge.service.set_sidetone(level)
        self.schedule_save()

    def set_eq(self, gains: list[float], preset: str | None = None) -> None:
        prof = self.profile
        prof.eq_gains = fit_gains(gains, self.layout)
        prof.eq_freqs = list(self.layout.freqs)
        prof.eq_preset = preset or match_preset(prof.eq_gains, self.config.user_eq_presets, self.layout)
        self._push_desired()
        self.bridge.service.set_equalizer(prof.eq_gains)
        self.eqChanged.emit(list(prof.eq_gains), prof.eq_preset)
        self.schedule_save()

    def apply_eq_preset(self, key: str) -> None:
        if key.startswith("user:"):
            gains = self.config.user_eq_presets.get(key[5:])
        else:
            preset = PRESETS_BY_KEY.get(key)
            gains = list(preset.gains) if preset else None
        if gains is not None:
            self.set_eq(fit_gains(gains, self.layout), key)

    def save_user_preset(self, name: str) -> None:
        self.config.user_eq_presets[name] = list(self.profile.eq_gains)
        self.profile.eq_preset = f"user:{name}"
        self.userPresetsChanged.emit()
        self.eqChanged.emit(list(self.profile.eq_gains), self.profile.eq_preset)
        self.schedule_save()

    def delete_user_preset(self, name: str) -> None:
        self.config.user_eq_presets.pop(name, None)
        if self.profile.eq_preset == f"user:{name}":
            self.profile.eq_preset = match_preset(self.profile.eq_gains, self.config.user_eq_presets, self.layout)
        self.userPresetsChanged.emit()
        self.eqChanged.emit(list(self.profile.eq_gains), self.profile.eq_preset)
        self.schedule_save()

    # -- headset controls (HeadsetControl-port devices) ----------------------------------------------
    def set_control(self, name: str, value: object) -> None:
        if name not in ACTIONS:
            self.profile.controls[name] = value
            self._push_desired()
            self.schedule_save()
        self.bridge.service.set_control(name, value)

    # -- device power / lighting ----------------------------------------------------------------
    def set_auto_off(self, minutes: int) -> None:
        self.config.auto_off_minutes = int(minutes)
        self._push_desired()
        self.bridge.service.set_auto_off(minutes)
        self.schedule_save()

    def update_lighting(self, **changes) -> None:
        light = self.profile.lighting
        for k, val in changes.items():
            setattr(light, k, val)
        self._push_desired()
        self.bridge.service.set_lights(self._lights_payload())
        self.lightingChanged.emit()
        self.schedule_save()

    # -- voice / surround -------------------------------------------------------------------
    def update_voice(self, **changes) -> None:
        v = self.profile.voice
        preset = changes.pop("preset", None)
        if preset:
            apply_voice_preset(v, preset)
        for k, val in changes.items():
            setattr(v, k, val)
        v.preset = voice_matches_preset(v)
        self.audio.apply_voice(v, "Open Headset · " + tr("Voice enhancement"))
        self.voiceChanged.emit()
        self.schedule_save()

    def update_surround(self, **changes) -> None:
        s = self.profile.surround
        for k, val in changes.items():
            setattr(s, k, val)
        self.audio.apply_surround(s, "Open Headset · " + tr("Virtual 7.1 surround"))
        self.surroundChanged.emit()
        self.schedule_save()

    # -- profiles ----------------------------------------------------------------------------
    def select_profile(self, name: str) -> None:
        if name not in self.config.profiles:
            return
        self.config.active_profile = name
        prof = self.profile
        if prof.eq_freqs != list(self.layout.freqs):
            prof.eq_gains = fit_gains(prof.eq_gains, self.layout, tuple(prof.eq_freqs))
            prof.eq_freqs = list(self.layout.freqs)
        self._push_desired()
        if self.bridge.state is LinkState.CONNECTED:
            self.bridge.service.set_sidetone(prof.sidetone)
            self.bridge.service.set_equalizer(prof.eq_gains)
            self.bridge.service.set_lights(self._lights_payload())
            dev = self.bridge.device
            for name, value in prof.controls.items():
                if dev is not None and name in dev.controls and name not in ACTIONS:
                    self.bridge.service.set_control(name, value)
        self._apply_effects()
        self.profileChanged.emit()
        self.eqChanged.emit(list(prof.eq_gains), prof.eq_preset)
        self.sidetoneChanged.emit(prof.sidetone)
        self.voiceChanged.emit()
        self.surroundChanged.emit()
        self.lightingChanged.emit()
        self.schedule_save()

    def new_profile(self, name: str, copy_current: bool) -> None:
        self.config.add_profile(name, self.config.active_profile if copy_current else None)
        self.select_profile(name)

    def rename_profile(self, old: str, new: str) -> None:
        self.config.rename_profile(old, new)
        self.profileChanged.emit()
        self.schedule_save()

    def delete_profile(self, name: str) -> None:
        self.config.delete_profile(name)
        self.select_profile(self.config.active_profile)

    # -- app settings ------------------------------------------------------------------------
    def update_settings(self, **changes) -> None:
        app = self.config.app
        for k, val in changes.items():
            setattr(app, k, val)
        if "battery_poll_seconds" in changes:
            self.bridge.service.battery_interval = float(app.battery_poll_seconds)
        if "apply_on_connect" in changes:
            self._push_desired()
        if "auto_check_updates" in changes:
            self.updates.set_auto(app.auto_check_updates)
        self.settingsChanged.emit()
        self.schedule_save()
