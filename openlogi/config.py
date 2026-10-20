"""Persistent settings and profiles (``~/.config/openlogi-headset/config.json``)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

APP_ID = "openlogi-headset"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_ID
CONFIG_FILE = CONFIG_DIR / "config.json"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir())) / APP_ID


def _from_dict(cls, data: dict[str, Any] | None):
    """Build a dataclass from a dict, ignoring unknown keys (forward compatible)."""
    obj = cls()
    if not isinstance(data, dict):
        return obj
    for f in fields(cls):
        if f.name not in data:
            continue
        current = getattr(obj, f.name)
        value = data[f.name]
        if hasattr(current, "__dataclass_fields__"):
            value = _from_dict(type(current), value)
        elif isinstance(current, bool):
            value = bool(value)
        elif isinstance(current, (int, float)) and not isinstance(value, (int, float)):
            continue
        setattr(obj, f.name, value)
    return obj


def _numbers(value: Any) -> bool:
    return (isinstance(value, list) and 0 < len(value) <= 32
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value))


@dataclass(slots=True)
class VoiceSettings:
    """Software "Blue VO!CE"-style microphone chain (PipeWire filter-chain)."""

    enabled: bool = False
    make_default: bool = True
    highpass_enabled: bool = True
    highpass_hz: int = 90
    noise_suppression: bool = True
    gate_enabled: bool = True
    gate_threshold_db: float = -50.0
    bass_db: float = 0.0
    presence_db: float = 3.0
    air_db: float = 1.5
    deesser_db: float = -3.0
    gain_db: float = 0.0
    preset: str = "broadcast"


@dataclass(slots=True)
class SurroundSettings:
    """Virtual 7.1 surround sink (HRTF via PipeWire SOFA spatializer)."""

    enabled: bool = False
    make_default: bool = True
    room: str = "kemar"  # SOFA dataset key
    gain_db: float = 0.0


@dataclass(slots=True)
class LightingSettings:
    """RGB lighting for models with the 0x8070 colour-LED feature (all zones alike)."""

    mode: str = "static"  # off | static | breathing
    color: str = "#00b4f5"
    brightness: int = 100

    def rgb(self) -> tuple[int, int, int]:
        try:
            raw = bytes.fromhex(self.color.lstrip("#")[:6])
            return raw[0], raw[1], raw[2]
        except (ValueError, IndexError):
            return 0, 180, 245


@dataclass(slots=True)
class Profile:
    name: str = "Default"
    eq_preset: str = "flat"
    eq_gains: list[float] = field(default_factory=lambda: [0] * 10)  # in the band layout of eq_freqs
    eq_freqs: list[int] = field(default_factory=lambda: [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000])
    sidetone: int = 0
    controls: dict[str, Any] = field(default_factory=dict)  # headset controls (voice prompts, mic volume…)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    surround: SurroundSettings = field(default_factory=SurroundSettings)
    lighting: LightingSettings = field(default_factory=LightingSettings)


@dataclass(slots=True)
class AppSettings:
    language: str = "auto"
    start_minimized: bool = False
    close_to_tray: bool = True
    apply_on_connect: bool = True
    notify_connection: bool = True
    notify_low_battery: bool = True
    low_battery_percent: int = 15
    notify_full_charge: bool = True
    battery_poll_seconds: int = 10
    auto_check_updates: bool = True


CONFIG_VERSION = 2


@dataclass(slots=True)
class Config:
    version: int = CONFIG_VERSION
    app: AppSettings = field(default_factory=AppSettings)
    active_profile: str = "Default"
    profiles: dict[str, Profile] = field(default_factory=lambda: {"Default": Profile()})
    user_eq_presets: dict[str, list[float]] = field(default_factory=dict)
    auto_off_minutes: int | None = None  # None: leave the headset's own setting alone

    # -- profile helpers -------------------------------------------------------
    @property
    def profile(self) -> Profile:
        if self.active_profile not in self.profiles:
            self.active_profile = next(iter(self.profiles))
        return self.profiles[self.active_profile]

    def add_profile(self, name: str, copy_from: str | None = None) -> Profile:
        base = self.profiles.get(copy_from or "")
        prof = _from_dict(Profile, asdict(base)) if base else Profile()
        prof.name = name
        self.profiles[name] = prof
        return prof

    def rename_profile(self, old: str, new: str) -> None:
        prof = self.profiles.pop(old)
        prof.name = new
        self.profiles[new] = prof
        if self.active_profile == old:
            self.active_profile = new

    def delete_profile(self, name: str) -> None:
        if len(self.profiles) <= 1:
            return
        self.profiles.pop(name, None)
        if self.active_profile == name:
            self.active_profile = next(iter(self.profiles))

    # -- (de)serialisation -----------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Config:
        cfg = cls()
        cfg.app = _from_dict(AppSettings, data.get("app"))
        if int(data.get("version", 1)) < 2 and cfg.app.battery_poll_seconds == 30:
            cfg.app.battery_poll_seconds = 10  # v1 default was too slow to notice plug/unplug
        cfg.active_profile = str(data.get("active_profile", cfg.active_profile))
        profiles = data.get("profiles")
        if isinstance(profiles, dict) and profiles:
            cfg.profiles = {}
            for name, raw in profiles.items():
                prof = _from_dict(Profile, raw)
                prof.name = name
                if not _numbers(prof.eq_gains) or not _numbers(prof.eq_freqs) or len(prof.eq_freqs) != len(
                        prof.eq_gains):
                    prof.eq_gains, prof.eq_freqs = [0] * 10, list(Profile().eq_freqs)
                if not isinstance(prof.controls, dict):
                    prof.controls = {}
                cfg.profiles[name] = prof
        auto_off = data.get("auto_off_minutes")
        cfg.auto_off_minutes = int(auto_off) if isinstance(auto_off, (int, float)) else None
        presets = data.get("user_eq_presets")
        if isinstance(presets, dict):
            cfg.user_eq_presets = {str(k): list(v) for k, v in presets.items() if _numbers(v)}
        return cfg

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> Config:
        try:
            return cls.from_json(json.loads(path.read_text()))
        except (OSError, ValueError):
            return cls()

    def save(self, path: Path = CONFIG_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(self.to_json(), fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
