"""Virtual 7.1 surround for headphones (DTS Headphone:X-style).

An 8-channel virtual sink whose channels are each rendered through an HRTF
(PipeWire's SOFA spatializer) at its loudspeaker position and down-mixed to
binaural stereo on the headset.
"""

from __future__ import annotations

from pathlib import Path

from ..config import SurroundSettings
from .filterchain import FilterProcess, build_conf, module_block

SURROUND_NODE = "openheadset_surround"

# ITU-R BS.775 layout; azimuth counter-clockwise from front, as the SOFA plugin expects.
CHANNELS: tuple[tuple[str, float, float], ...] = (
    ("FL", 30.0, 0.0),
    ("FR", 330.0, 0.0),
    ("FC", 0.0, 0.0),
    ("LFE", 0.0, -20.0),
    ("RL", 150.0, 0.0),
    ("RR", 210.0, 0.0),
    ("SL", 90.0, 0.0),
    ("SR", 270.0, 0.0),
)

SOFA_SEARCH = (
    Path.home() / ".local/share/pipewire/hrtf-sofa",
    Path("/usr/share/libmysofa"),
    Path("/usr/local/share/libmysofa"),
)

SOFA_ROOMS = {
    "kemar": "MIT_KEMAR_normal_pinna.sofa",
    "default": "default.sofa",
}


def available_rooms() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for key, fname in SOFA_ROOMS.items():
        for base in SOFA_SEARCH:
            if (base / fname).is_file():
                found[key] = base / fname
                break
    # any extra user-provided datasets
    user_dir = SOFA_SEARCH[0]
    if user_dir.is_dir():
        for f in sorted(user_dir.glob("*.sofa")):
            found.setdefault(f.stem, f)
    return found


def build_surround_conf(s: SurroundSettings, sink_node: str, sofa: Path, description: str) -> str:
    gain = 10.0 ** (s.gain_db / 20.0) * 0.6  # headroom: 8 channels summed into 2
    nodes: list[dict] = []
    links: list[dict] = []
    for i, (ch, az, el) in enumerate(CHANNELS, start=1):
        nodes.append(
            {
                "type": "sofa",
                "label": "spatializer",
                "name": f"sp{ch}",
                "config": {"filename": str(sofa)},
                "control": {"Azimuth": az, "Elevation": el, "Radius": 3.0},
            }
        )
        links.append({"output": f"sp{ch}:Out L", "input": f"mixL:In {i}"})
        links.append({"output": f"sp{ch}:Out R", "input": f"mixR:In {i}"})
    mix_ctl = {f"Gain {i}": gain for i in range(1, 9)}
    nodes.append({"type": "builtin", "label": "mixer", "name": "mixL", "control": mix_ctl})
    nodes.append({"type": "builtin", "label": "mixer", "name": "mixR", "control": mix_ctl})
    args = {
        "node.description": description,
        "media.name": description,
        "filter.graph": {
            "nodes": nodes,
            "links": links,
            "inputs": [f"sp{ch}:In" for ch, _, _ in CHANNELS],
            "outputs": ["mixL:Out", "mixR:Out"],
        },
        "capture.props": {
            "node.name": SURROUND_NODE,
            "node.description": description,
            "media.class": "Audio/Sink",
            "audio.channels": 8,
            "audio.position": [ch for ch, _, _ in CHANNELS],
            "device.icon-name": "audio-headphones",
        },
        "playback.props": {
            "node.name": f"{SURROUND_NODE}.output",
            "node.passive": True,
            "target.object": sink_node,
            "audio.channels": 2,
            "audio.position": ["FL", "FR"],
            "stream.dont-remix": True,
        },
    }
    return build_conf(module_block("libpipewire-module-filter-chain", args))


class SurroundSink:
    def __init__(self):
        self.proc = FilterProcess("surround")

    @property
    def running(self) -> bool:
        return self.proc.running

    def apply(self, s: SurroundSettings, sink_node: str | None, description: str) -> bool:
        rooms = available_rooms()
        sofa = rooms.get(s.room) or next(iter(rooms.values()), None)
        if not s.enabled or not sink_node or sofa is None:
            self.stop()
            return sofa is not None
        self.proc.start(build_surround_conf(s, sink_node, sofa, description))
        return True

    def stop(self) -> None:
        self.proc.stop()
