"""Voltage → state-of-charge estimation for the PRO X Wireless Li-ion cell."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

# (millivolts, percent) discharge curve, ascending voltage. Derived from the
# calibration points used by G HUB / HeadsetControl for the G PRO family,
# densified so the estimate is smooth instead of jumping between knees.
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

# While charging the cell voltage reads ~80 mV high; compensate so the gauge
# does not leap upward when the cable is plugged in.
CHARGING_OFFSET_MV = 80

FULL_HOURS = 20.0  # Logitech's rated battery life for the PRO X Wireless
CHARGE_HOURS = 3.0


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
    """Smooths noisy ADC readings with an exponential moving average."""

    def __init__(self, alpha: float = 0.35):
        self.alpha = alpha
        self._mv: float | None = None
        self._charging: bool | None = None

    @property
    def empty(self) -> bool:
        return self._mv is None

    def reset(self) -> None:
        self._mv = None
        self._charging = None

    def update(self, millivolts: int, charging: bool, complete: bool = False) -> BatteryState:
        # Restart the average whenever the charge state flips: the voltage
        # steps abruptly in that case and smoothing would only lag.
        if self._mv is None or charging != self._charging:
            self._mv = float(millivolts)
        else:
            self._mv += self.alpha * (millivolts - self._mv)
        self._charging = charging
        pct = percent_from_millivolts(self._mv, charging)
        full = complete or (charging and pct >= 99.5)
        if full:
            pct = 100.0
        return BatteryState(round(pct), millivolts, charging, full)
