"""Vendor drivers ported from HeadsetControl (non-HID++ protocols)."""

from . import corsair, hyperx, misc, steelseries  # noqa: F401  (importing registers the drivers)
from .base import REGISTRY, Cap, Driver, DriverError  # noqa: F401
