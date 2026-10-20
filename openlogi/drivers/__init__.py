"""Vendor drivers ported from HeadsetControl (non-HID++ protocols)."""

from . import hyperx, steelseries  # noqa: F401  (importing registers the drivers)
from .base import REGISTRY, Cap, Driver, DriverError  # noqa: F401
