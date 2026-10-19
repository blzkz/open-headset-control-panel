"""Headset sink/source volume, mute and default-device control through ``pactl``.

``pactl`` speaks to pipewire-pulse (or PulseAudio) and offers JSON output, which
keeps this module dependency-free and robust across distributions.
"""

from __future__ import annotations

import json
import math
import random
import shutil
import struct
import subprocess
import wave
from dataclasses import dataclass, field

from ..config import RUNTIME_DIR


@dataclass(slots=True)
class Endpoint:
    name: str
    description: str
    volume: int  # percent
    muted: bool


@dataclass(slots=True)
class AudioState:
    sink: Endpoint | None = None
    source: Endpoint | None = None
    default_sink: str = ""
    default_source: str = ""
    descriptions: dict[str, str] = field(default_factory=dict)


def pactl_available() -> bool:
    return shutil.which("pactl") is not None


def _pactl(*args: str, json_out: bool = False, timeout: float = 3.0):
    cmd = ["pactl", *(["-f", "json"] if json_out else []), *args]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    if json_out:
        try:
            return json.loads(res.stdout or "null")
        except ValueError:
            return None
    return res.stdout.strip()


def _volume_percent(vol: dict) -> int:
    values = []
    for ch in vol.values():
        try:
            values.append(int(str(ch.get("value_percent", "0")).rstrip("%")))
        except ValueError:
            pass
    return round(sum(values) / len(values)) if values else 0


def _matches(props: dict, vendor_id: int, product_id: int) -> bool:
    try:
        return int(props.get("device.vendor.id", "0"), 16) == vendor_id and int(
            props.get("device.product.id", "0"), 16
        ) == product_id
    except ValueError:
        return False


def _find(items: list, vendor_id: int, product_id: int) -> Endpoint | None:
    for item in items:
        props = item.get("properties", {})
        if props.get("device.class") == "monitor":
            continue
        if _matches(props, vendor_id, product_id):
            return Endpoint(
                name=item["name"],
                description=item.get("description", item["name"]),
                volume=_volume_percent(item.get("volume", {})),
                muted=bool(item.get("mute")),
            )
    return None


def query(vendor_id: int, product_id: int) -> AudioState:
    sinks = _pactl("list", "sinks", json_out=True) or []
    sources = _pactl("list", "sources", json_out=True) or []
    st = AudioState(
        sink=_find(sinks, vendor_id, product_id),
        source=_find(sources, vendor_id, product_id),
        default_sink=_pactl("get-default-sink") or "",
        default_source=_pactl("get-default-source") or "",
    )
    st.descriptions = {i["name"]: i.get("description", i["name"]) for i in (*sinks, *sources) if "name" in i}
    return st


def set_volume(kind: str, name: str, percent: int) -> None:
    percent = max(0, min(150, int(percent)))
    _pactl(f"set-{kind}-volume", name, f"{percent}%")


def set_mute(kind: str, name: str, muted: bool) -> None:
    _pactl(f"set-{kind}-mute", name, "1" if muted else "0")


def set_default(kind: str, name: str) -> None:
    _pactl(f"set-default-{kind}", name)


def get_default(kind: str) -> str:
    return _pactl(f"get-default-{kind}") or ""


def node_exists(kind: str, name: str) -> bool:
    out = _pactl("list", "short", kind) or ""
    return any(line.split("\t")[1:2] == [name] for line in out.splitlines())


# -- channel test tones ------------------------------------------------------------
CHANNEL_ORDER = ("FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR")


def _test_tone(channel: str) -> str:
    """Write (once) an 8-channel WAV with a pink-noise burst on a single channel."""
    path = RUNTIME_DIR / f"test-{channel}.wav"
    if path.exists():
        return str(path)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    rate, seconds = 48000, 0.9
    idx = CHANNEL_ORDER.index(channel)
    rng = random.Random(idx)
    b = [0.0] * 7
    frames = bytearray()
    n = int(rate * seconds)
    for i in range(n):
        if channel == "LFE":
            s = math.sin(2 * math.pi * 60 * i / rate) * 0.5
        else:  # Paul Kellet's pink-noise filter
            w = rng.uniform(-1, 1)
            b[0] = 0.99886 * b[0] + w * 0.0555179
            b[1] = 0.99332 * b[1] + w * 0.0750759
            b[2] = 0.96900 * b[2] + w * 0.1538520
            b[3] = 0.86650 * b[3] + w * 0.3104856
            b[4] = 0.55000 * b[4] + w * 0.5329522
            b[5] = -0.7616 * b[5] - w * 0.0168980
            s = (sum(b[:6]) + b[6] + w * 0.5362) * 0.08
            b[6] = w * 0.115926
        env = min(1.0, i / 2400, (n - i) / 4800)
        sample = int(max(-1.0, min(1.0, s * env)) * 32767)
        frame = [0] * 8
        frame[idx] = sample
        frames += struct.pack("<8h", *frame)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(8)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return str(path)


def play_channel_test(target_node: str, channel: str) -> subprocess.Popen | None:
    if shutil.which("pw-play") is None or channel not in CHANNEL_ORDER:
        return None
    return subprocess.Popen(
        [
            "pw-play",
            "--target", target_node,
            "--channels", "8",
            "--channel-map", ",".join(CHANNEL_ORDER),
            _test_tone(channel),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
