"""Software microphone enhancement, in the spirit of Blue VO!CE.

Graph (mono)::

    headset mic ─▶ [WebRTC noise suppression] ─▶ high-pass ─▶ noise gate
               ─▶ low-shelf ─▶ presence peak ─▶ de-esser ─▶ air shelf ─▶ gain ─▶ virtual source
"""

from __future__ import annotations

from dataclasses import asdict

from ..config import VoiceSettings
from .filterchain import (
    FilterProcess,
    build_conf,
    builtin_filter_available,
    find_node_id,
    module_block,
    set_controls,
)

VOICE_NODE = "openlogi_voice"
NS_NODE = "openlogi_voice_ns"

VOICE_PRESETS: dict[str, dict[str, float | bool | int]] = {
    "broadcast": dict(highpass_enabled=True, highpass_hz=90, gate_enabled=True, gate_threshold_db=-50,
                      bass_db=2.0, presence_db=3.0, deesser_db=-4.0, air_db=2.0, gain_db=2.0),
    "natural": dict(highpass_enabled=True, highpass_hz=70, gate_enabled=False, gate_threshold_db=-55,
                    bass_db=0.0, presence_db=1.0, deesser_db=-2.0, air_db=0.5, gain_db=0.0),
    "clear": dict(highpass_enabled=True, highpass_hz=120, gate_enabled=True, gate_threshold_db=-48,
                  bass_db=-2.0, presence_db=4.5, deesser_db=-3.0, air_db=3.0, gain_db=1.0),
    "warm": dict(highpass_enabled=True, highpass_hz=60, gate_enabled=True, gate_threshold_db=-52,
                 bass_db=4.0, presence_db=0.5, deesser_db=-5.0, air_db=-1.0, gain_db=1.5),
    "streamer": dict(highpass_enabled=True, highpass_hz=100, gate_enabled=True, gate_threshold_db=-42,
                     bass_db=1.0, presence_db=3.5, deesser_db=-5.0, air_db=2.5, gain_db=3.0),
}


def db_to_lin(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _controls(s: VoiceSettings) -> dict[str, dict[str, float]]:
    gate_open = db_to_lin(s.gate_threshold_db) if s.gate_enabled else 0.0
    return {
        "hp": {"Freq": float(s.highpass_hz if s.highpass_enabled else 10), "Q": 0.707},
        "gate": {
            "Open Threshold": gate_open,
            "Close Threshold": gate_open * 0.6,
            "Attack (s)": 0.004,
            "Hold (s)": 0.20,
            "Release (s)": 0.15,
        },
        "bass": {"Freq": 160.0, "Q": 0.7, "Gain": float(s.bass_db)},
        "presence": {"Freq": 3200.0, "Q": 0.9, "Gain": float(s.presence_db)},
        "deess": {"Freq": 6800.0, "Q": 3.0, "Gain": float(s.deesser_db)},
        "air": {"Freq": 11000.0, "Q": 0.7, "Gain": float(s.air_db)},
        "gain": {"Gain 1": db_to_lin(s.gain_db)},
    }


_LABELS = {
    "hp": "bq_highpass",
    "gate": "noisegate",
    "bass": "bq_lowshelf",
    "presence": "bq_peaking",
    "deess": "bq_peaking",
    "air": "bq_highshelf",
    "gain": "mixer",
}


def _graph_order() -> list[str]:
    # The builtin noise gate only exists in recent PipeWire (1.4+); skip it elsewhere.
    return [n for n in _LABELS if n != "gate" or builtin_filter_available("noisegate")]


def build_voice_conf(s: VoiceSettings, mic_node: str, description: str) -> str:
    controls = _controls(s)
    order = _graph_order()
    nodes = [{"type": "builtin", "label": _LABELS[n], "name": n, "control": controls[n]} for n in order]
    links = [
        {"output": f"{a}:Out", "input": f"{b}:{'In 1' if b == 'gain' else 'In'}"}
        for a, b in zip(order, order[1:], strict=False)
    ]
    blocks = []
    capture_target = mic_node
    if s.noise_suppression:
        blocks.append(
            module_block(
                "libpipewire-module-echo-cancel",
                {
                    "library.name": "aec/libspa-aec-webrtc",
                    "monitor.mode": True,
                    "audio.channels": 1,
                    "audio.position": ["MONO"],
                    "aec.args": {
                        "webrtc.noise_suppression": True,
                        "webrtc.high_pass_filter": True,
                        "webrtc.gain_control": False,
                        "webrtc.extended_filter": False,
                        "webrtc.delay_agnostic": True,
                    },
                    "capture.props": {"node.name": f"{NS_NODE}.capture", "target.object": mic_node,
                                      "node.passive": True},
                    "source.props": {"node.name": NS_NODE, "node.description": f"{description} (NS)",
                                     "node.virtual": True},
                },
            )
        )
        capture_target = NS_NODE
    blocks.append(
        module_block(
            "libpipewire-module-filter-chain",
            {
                "node.description": description,
                "media.name": description,
                "filter.graph": {
                    "nodes": nodes,
                    "links": links,
                    "inputs": [f"{order[0]}:In"],
                    "outputs": ["gain:Out"],
                },
                "audio.channels": 1,
                "audio.position": ["MONO"],
                "capture.props": {
                    "node.name": f"{VOICE_NODE}.capture",
                    "node.passive": True,
                    "target.object": capture_target,
                    "stream.dont-remix": True,
                },
                "playback.props": {
                    "node.name": VOICE_NODE,
                    "node.description": description,
                    "media.class": "Audio/Source",
                    "device.icon-name": "audio-input-microphone",
                },
            },
        )
    )
    return build_conf(*blocks)


class VoiceChain:
    def __init__(self):
        self.proc = FilterProcess("voice")
        self._structure: tuple | None = None
        self._node_id: int | None = None

    @property
    def running(self) -> bool:
        return self.proc.running

    def apply(self, s: VoiceSettings, mic_node: str | None, description: str) -> None:
        if not s.enabled or not mic_node:
            self.stop()
            return
        structure = (s.noise_suppression, mic_node, description)
        if self.running and structure == self._structure:
            # Only control values changed: update live so recording apps keep their stream.
            if self._node_id is None:
                self._node_id = find_node_id(VOICE_NODE + ".capture", timeout=1.0)
            if self._node_id is not None:
                order = _graph_order()
                flat = {f"{n}:{k}": v for n, ctl in _controls(s).items() if n in order for k, v in ctl.items()}
                if set_controls(self._node_id, flat):
                    return
        self._structure = structure
        self._node_id = None
        self.proc.start(build_voice_conf(s, mic_node, description))

    def stop(self) -> None:
        self.proc.stop()
        self._structure = None
        self._node_id = None


def apply_preset(s: VoiceSettings, key: str) -> None:
    values = VOICE_PRESETS.get(key)
    if not values:
        return
    for k, v in values.items():
        setattr(s, k, v)
    s.preset = key


def matches_preset(s: VoiceSettings) -> str:
    data = asdict(s)
    for key, values in VOICE_PRESETS.items():
        if all(abs(float(data[k]) - float(v)) < 1e-6 for k, v in values.items()):
            return key
    return "custom"
