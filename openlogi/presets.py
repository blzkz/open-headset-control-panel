"""Built-in equalizer presets for the 10-band onboard EQ (32 Hz … 16 kHz, ±12 dB)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EqPreset:
    key: str
    name: str  # translation key, see i18n
    gains: tuple[int, ...]
    builtin: bool = True


BUILTIN_PRESETS: tuple[EqPreset, ...] = (
    EqPreset("flat", "Flat", (0, 0, 0, 0, 0, 0, 0, 0, 0, 0)),
    EqPreset("bass_boost", "Bass Boost", (7, 6, 5, 3, 1, 0, 0, 0, 0, 0)),
    EqPreset("fps", "FPS (footsteps)", (-4, -3, -2, -1, 0, 2, 4, 5, 3, 0)),
    EqPreset("moba", "MOBA", (2, 2, 1, 0, 0, 1, 2, 3, 2, 1)),
    EqPreset("movie", "Movie", (5, 4, 3, 1, 0, 0, 1, 2, 3, 2)),
    EqPreset("music", "Music", (4, 3, 2, 0, -1, -1, 1, 2, 3, 4)),
    EqPreset("voice", "Voice / Podcast", (-5, -3, -1, 1, 3, 4, 4, 2, 0, -2)),
    EqPreset("treble", "Treble Boost", (0, 0, 0, 0, 0, 1, 3, 5, 6, 6)),
    EqPreset("pro", "Pro Esports", (-2, -1, 0, 0, 1, 2, 3, 3, 2, 1)),
)

PRESETS_BY_KEY = {p.key: p for p in BUILTIN_PRESETS}
CUSTOM_KEY = "custom"


def match_preset(gains: list[int], custom: dict[str, list[int]] | None = None) -> str:
    """Return the key of the preset whose gains match exactly, else ``custom``."""
    t = tuple(gains)
    for p in BUILTIN_PRESETS:
        if p.gains == t:
            return p.key
    for name, g in (custom or {}).items():
        if tuple(g) == t:
            return f"user:{name}"
    return CUSTOM_KEY
