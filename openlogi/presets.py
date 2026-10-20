"""Built-in equalizer presets for the 10-band onboard EQ (32 Hz … 16 kHz, ±12 dB).

Headsets with another band layout get the same curves resampled (see :func:`fit_gains`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

STANDARD_FREQS: tuple[int, ...] = (32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)


@dataclass(frozen=True, slots=True)
class EqLayout:
    """Band centres, gain range and step of the connected headset's equalizer."""

    freqs: tuple[int, ...] = STANDARD_FREQS
    lo: float = -12.0
    hi: float = 12.0
    step: float = 1.0

    def snap(self, value: float) -> float:
        value = max(self.lo, min(self.hi, float(value)))
        snapped = round(round(value / self.step) * self.step, 4)
        return float(int(snapped)) if snapped == int(snapped) else snapped


def default_freqs(n: int) -> tuple[int, ...]:
    if n == len(STANDARD_FREQS):
        return STANDARD_FREQS
    if n <= 1:
        return (1000,) * n
    return tuple(int(round(32.0 * 500.0 ** (i / (n - 1)), -1 if i else 0)) for i in range(n))


def _interpolate(gains: list[float], src: tuple[int, ...], f: float) -> float:
    xs = [math.log(x) for x in src]
    x = math.log(f)
    if x <= xs[0]:
        return gains[0]
    if x >= xs[-1]:
        return gains[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            t = (x - xs[i]) / (xs[i + 1] - xs[i])
            return gains[i] + (gains[i + 1] - gains[i]) * t
    return gains[-1]


def fit_gains(gains, layout: EqLayout, src_freqs: tuple[int, ...] | None = None) -> list[float]:
    """Express a curve in ``layout``: same band count maps by index, otherwise by frequency."""
    gains = [float(g) for g in gains]
    if not gains:
        return [0.0] * len(layout.freqs)
    if len(gains) != len(layout.freqs):
        src = tuple(src_freqs) if src_freqs and len(src_freqs) == len(gains) else default_freqs(len(gains))
        gains = [_interpolate(gains, src, f) for f in layout.freqs]
    return [layout.snap(g) for g in gains]


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


def match_preset(gains, custom: dict[str, list] | None = None, layout: EqLayout | None = None) -> str:
    """Return the key of the preset whose gains match exactly (in ``layout``), else ``custom``."""
    layout = layout or EqLayout(default_freqs(len(gains)))
    t = [float(g) for g in gains]
    for p in BUILTIN_PRESETS:
        if fit_gains(p.gains, layout) == t:
            return p.key
    for name, g in (custom or {}).items():
        if fit_gains(g, layout) == t:
            return f"user:{name}"
    return CUSTOM_KEY
