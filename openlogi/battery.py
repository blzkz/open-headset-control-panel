"""State-of-charge estimation for the PRO X Wireless Li-ion cell.

The headset only reports a cell voltage plus a power flag (0x01 battery,
0x03 external power). Measured on the real device:

* resting under load ~3.80 V, the same cell reads ~3.98 V while charging (+170 mV);
* for a few seconds around plugging/unplugging the readings are garbage;
* a worn plug toggles the power flag repeatedly.

So a naive voltage→percent mapping jumps wildly (13 % on plug-in, 64 % on unplug).
The estimator below therefore:

* debounces the power flag and ignores voltage right after every transition;
* while charging, advances the percentage with time instead of trusting voltage;
* while discharging, follows a smoothed voltage but never rises again unless the
  gap is large and persistent (a wrong initial guess), so the gauge is monotonic;
* remembers the last value across restarts.
"""

from __future__ import annotations

import json
import os
import time
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path

# (millivolts, percent) open-circuit discharge curve, ascending voltage.
DISCHARGE_CURVE: tuple[tuple[int, float], ...] = (
    (3320, 0.0),
    (3550, 2.0),
    (3670, 5.0),
    (3710, 12.0),
    (3740, 20.0),
    (3780, 30.0),
    (3805, 40.0),
    (3830, 50.0),
    (3880, 60.0),
    (3940, 70.0),
    (4000, 80.0),
    (4075, 90.0),
    (4150, 100.0),
)

CHARGING_OFFSET_MV = 170  # measured: charge voltage minus resting voltage
FULL_HOURS = 20.0  # Logitech's rated battery life
CHARGE_HOURS = 3.0  # empty → full on USB
FULL_CHARGE_MV = 4180  # CV phase reached: the cell is (almost) full

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "openlogi-headset"


def percent_from_millivolts(mv: float, charging: bool = False) -> float:
    if charging:
        mv -= CHARGING_OFFSET_MV
    volts = [v for v, _ in DISCHARGE_CURVE]
    if mv <= volts[0]:
        return 0.0
    if mv >= volts[-1]:
        return 100.0
    i = bisect_left(volts, mv)
    (v0, p0), (v1, p1) = DISCHARGE_CURVE[i - 1], DISCHARGE_CURVE[i]
    return p0 + (p1 - p0) * (mv - v0) / (v1 - v0)


@dataclass(frozen=True, slots=True)
class BatteryState:
    percent: int
    millivolts: int
    charging: bool
    full: bool

    @property
    def remaining_hours(self) -> float | None:
        if self.charging:
            return None
        return FULL_HOURS * self.percent / 100.0

    @property
    def hours_to_full(self) -> float | None:
        if not self.charging or self.full:
            return None
        return CHARGE_HOURS * (100 - self.percent) / 100.0


class BatteryEstimator:
    FLAG_DEBOUNCE_S = 8.0  # a power-flag change must persist this long
    SETTLE_S = 45.0  # ignore voltage this long after plugging / unplugging
    RAISE_GAP = 8.0  # discharging: only correct upwards by more than this…
    RAISE_VOTES = 4  # …seen in this many consecutive readings
    # A remembered percentage is only a useful anchor if it is fresh: after hours
    # with the app closed (e.g. charging overnight) the real level is anything.
    STALE_S = 10 * 60

    def __init__(self, alpha: float = 0.3, state_file: Path | None = None):
        self.alpha = alpha
        self.state_file = state_file
        self._pct: float | None = None
        self._mv: float | None = None
        self._charging: bool | None = None
        self._raw_flag: bool | None = None
        self._raw_since = 0.0
        self._transition_at = float("-inf")
        self._last_t: float | None = None
        self._pct_t: float | None = None  # wall time the percentage was last valid
        self._raise_votes = 0
        self._load()

    # -- persistence -------------------------------------------------------------------
    def _load(self) -> None:
        if self.state_file is None:
            return
        try:
            data = json.loads(self.state_file.read_text())
            self._pct = max(0.0, min(100.0, float(data["percent"])))
            self._pct_t = float(data["t"])
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save(self, now: float) -> None:
        if self.state_file is None or self._pct is None:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"percent": round(self._pct, 2), "t": now}))
        except OSError:
            pass

    # -- public ------------------------------------------------------------------------
    @property
    def empty(self) -> bool:
        return self._mv is None and self._pct is None

    @property
    def transition_pending(self) -> bool:
        """The raw power flag disagrees with the accepted one: poll again soon."""
        return self._raw_flag is not None and self._raw_flag != self._charging

    def reset(self) -> None:
        """Headset went away: forget the signal history but keep the last percentage."""
        self._mv = None
        self._charging = None
        self._raw_flag = None
        self._last_t = None
        self._raise_votes = 0

    def update(self, millivolts: int, charging: bool, complete: bool = False,
               now: float | None = None) -> BatteryState:
        now = time.time() if now is None else now
        dt = 0.0 if self._last_t is None else max(0.0, now - self._last_t)
        self._last_t = now
        if self._pct is not None and (self._pct_t is None or now - self._pct_t > self.STALE_S):
            self._pct = None  # stale anchor (app closed / headset off for a while): re-measure
        self._pct_t = now

        # 1. debounce the power flag
        if charging != self._raw_flag:
            self._raw_flag, self._raw_since = charging, now
        if self._charging is None:
            self._charging = charging
        elif charging != self._charging and now - self._raw_since >= self.FLAG_DEBOUNCE_S:
            self._charging = charging
            self._transition_at = now
            self._mv = None
            self._raise_votes = 0
        settling = now - self._transition_at < self.SETTLE_S or charging != self._charging

        # 2. update the estimate
        if self._charging:
            if self._pct is None:
                self._pct = 100.0 if complete else percent_from_millivolts(millivolts, charging=True)
            else:
                self._pct += dt / 3600.0 / CHARGE_HOURS * 100.0
            full = complete or (not settling and millivolts >= FULL_CHARGE_MV)
            self._pct = 100.0 if full else min(self._pct, 99.0)
        else:
            full = False
            if not settling:
                self._mv = float(millivolts) if self._mv is None else self._mv + self.alpha * (millivolts - self._mv)
                est = percent_from_millivolts(self._mv)
                if self._pct is None or est <= self._pct:
                    self._pct = est
                    self._raise_votes = 0
                elif est - self._pct > self.RAISE_GAP:
                    self._raise_votes += 1
                    if self._raise_votes >= self.RAISE_VOTES:
                        self._pct, self._raise_votes = est, 0
                else:
                    self._raise_votes = 0
            elif self._pct is None:
                self._pct = percent_from_millivolts(millivolts)

        self._save(now)
        return BatteryState(round(self._pct), millivolts, bool(self._charging), full)
