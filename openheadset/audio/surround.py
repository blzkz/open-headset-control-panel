"""Virtual 7.1 surround for headphones (DTS Headphone:X-style).

An 8-channel virtual sink whose channels are each rendered through an HRTF
(PipeWire's SOFA spatializer) at its loudspeaker position and down-mixed to
binaural stereo on the headset.

Bass management, like an AV receiver: the measured HRTFs carry almost no bass
(the MIT KEMAR set is ~20 dB down at 60 Hz relative to 1 kHz), and low
frequencies are not directional anyway. A Linkwitz-Riley crossover splits every
channel: the highs go through the HRTF, the lows straight to both ears (the two
branches sum in phase), and the LFE channel only takes the direct path instead
of being "placed" in front of the listener.
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


CROSSOVER_HZ = 150.0
# Level of the direct bass path relative to the HRTF path's gain: matches the midrange
# level of the HRTFs (measured ~-5.5 dB at 1 kHz), so the overall response stays even.
BASS_LEVEL = 0.66
LFE_LEVEL = BASS_LEVEL * 10.0 ** (3.0 / 20.0)  # LFE content is mastered expecting a boost


def build_surround_conf(s: SurroundSettings, sink_node: str, sofa: Path, description: str) -> str:
    return build_conf(module_block("libpipewire-module-filter-chain", surround_args(s, sink_node, sofa, description)))


def surround_args(s: SurroundSettings, sink_node: str, sofa: Path, description: str) -> dict:
    """Arguments of the filter-chain module (the graph is inspected by the tests)."""
    gain = 10.0 ** (s.gain_db / 20.0) * 0.6  # headroom: 8 channels summed into 2
    nodes: list[dict] = []
    links: list[dict] = []
    inputs: list[str] = []
    bass_gains: dict[str, float] = {}
    spatial = [(ch, az, el) for ch, az, el in CHANNELS if ch != "LFE"]
    def lr4(kind: str, name: str) -> None:
        """4th-order Linkwitz-Riley half: two Butterworth biquads in series (``name``a → ``name``b)."""
        for stage in "ab":
            nodes.append({"type": "builtin", "label": f"bq_{kind}", "name": f"{name}{stage}",
                          "control": {"Freq": CROSSOVER_HZ, "Q": 0.7071}})
        links.append({"output": f"{name}a:Out", "input": f"{name}b:In"})

    for i, (ch, _az, _el) in enumerate(CHANNELS, start=1):
        lr4("lowpass", f"lp{ch}")
        links.append({"output": f"lp{ch}b:Out", "input": f"bass:In {i}"})
        bass_gains[f"Gain {i}"] = gain * (LFE_LEVEL if ch == "LFE" else BASS_LEVEL)
    for i, (ch, az, el) in enumerate(spatial, start=1):
        # one sink channel feeds two paths: the HRTF and the direct bass
        nodes.append({"type": "builtin", "label": "copy", "name": f"in{ch}"})
        nodes.append(
            {
                "type": "sofa",
                "label": "spatializer",
                "name": f"sp{ch}",
                "config": {"filename": str(sofa)},
                "control": {"Azimuth": az, "Elevation": el, "Radius": 3.0},
            }
        )
        lr4("highpass", f"hp{ch}")
        links.append({"output": f"in{ch}:Out", "input": f"hp{ch}a:In"})
        links.append({"output": f"hp{ch}b:Out", "input": f"sp{ch}:In"})
        links.append({"output": f"in{ch}:Out", "input": f"lp{ch}a:In"})
        links.append({"output": f"sp{ch}:Out L", "input": f"mixL:In {i}"})
        links.append({"output": f"sp{ch}:Out R", "input": f"mixR:In {i}"})
    bass_in = len(spatial) + 1
    links.append({"output": "bass:Out", "input": f"mixL:In {bass_in}"})
    links.append({"output": "bass:Out", "input": f"mixR:In {bass_in}"})
    for ch, _az, _el in CHANNELS:
        inputs.append(f"lp{ch}a:In" if ch == "LFE" else f"in{ch}:In")
    mix_ctl = {f"Gain {i}": gain for i in range(1, bass_in)}
    mix_ctl[f"Gain {bass_in}"] = 1.0
    nodes.append({"type": "builtin", "label": "mixer", "name": "bass", "control": bass_gains})
    nodes.append({"type": "builtin", "label": "mixer", "name": "mixL", "control": mix_ctl})
    nodes.append({"type": "builtin", "label": "mixer", "name": "mixR", "control": dict(mix_ctl)})
    args = {
        "node.description": description,
        "media.name": description,
        "filter.graph": {
            "nodes": nodes,
            "links": links,
            "inputs": inputs,
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
    return args


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
