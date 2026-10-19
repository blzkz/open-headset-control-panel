"""Run PipeWire filter graphs as private ``pipewire -c <conf>`` client processes.

Each effect (voice chain, virtual surround) is a standalone PipeWire client, so
nothing is written into the user's PipeWire configuration and stopping the
effect is just terminating the process.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from ..config import RUNTIME_DIR

_CLIENT_HEADER = """\
context.properties = {
    log.level = 0
}
context.spa-libs = {
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}
"""

_BASE_MODULES = """\
    { name = libpipewire-module-rt flags = [ ifexists nofail ] }
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-adapter }
"""


def spa_value(value) -> str:
    """Serialise a Python value as SPA-JSON."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(float(value)) if isinstance(value, float) else str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, dict):
        inner = " ".join(f"{json.dumps(str(k))} = {spa_value(v)}" for k, v in value.items())
        return "{ " + inner + " }"
    if isinstance(value, (list, tuple)):
        return "[ " + " ".join(spa_value(v) for v in value) + " ]"
    raise TypeError(f"cannot serialise {type(value)!r}")


def build_conf(*module_blocks: str) -> str:
    return _CLIENT_HEADER + "context.modules = [\n" + _BASE_MODULES + "".join(module_blocks) + "]\n"


def module_block(name: str, args: dict) -> str:
    return f"    {{ name = {name}\n      args = {spa_value(args)}\n    }}\n"


def pipewire_available() -> bool:
    return shutil.which("pipewire") is not None


_BUILTIN_GLOBS = (
    "spa-0.2/filter-graph/libspa-filter-graph-plugin-builtin.so",  # PipeWire >= 1.4
    "pipewire-0.3/libpipewire-module-filter-chain.so",  # older releases bundle the builtins
)
_LIB_DIRS = ("/usr/lib", "/usr/lib64", "/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu")


@functools.cache
def builtin_filter_available(label: str) -> bool:
    """Whether this PipeWire ships a given builtin filter-chain plugin (e.g. ``noisegate``)."""
    needle = label.encode() + b"\0"
    for base in _LIB_DIRS:
        for rel in _BUILTIN_GLOBS:
            path = Path(base) / rel
            try:
                if needle in path.read_bytes():
                    return True
            except OSError:
                continue
    return False


class FilterProcess:
    """Lifecycle of one ``pipewire -c`` effect process."""

    def __init__(self, key: str):
        self.key = key
        self.conf_path = RUNTIME_DIR / f"{key}.conf"
        self.pid_path = RUNTIME_DIR / f"{key}.pid"
        self._proc: subprocess.Popen | None = None
        self._conf_text = ""
        self._kill_stale()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _kill_stale(self) -> None:
        """Terminate a leftover process from a previous crashed session."""
        try:
            pid = int(self.pid_path.read_text())
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except (OSError, ValueError):
            return
        if str(self.conf_path).encode() in cmdline:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        self.pid_path.unlink(missing_ok=True)

    def start(self, conf_text: str) -> None:
        if self.running and conf_text == self._conf_text:
            return
        self.stop()
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        self.conf_path.write_text(conf_text)
        self._conf_text = conf_text
        self._proc = subprocess.Popen(
            ["pipewire", "-c", str(self.conf_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.pid_path.write_text(str(self._proc.pid))

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        self._conf_text = ""
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.pid_path.unlink(missing_ok=True)


def find_node_id(node_name: str, timeout: float = 2.0) -> int | None:
    """Return the PipeWire global id of a node by ``node.name`` (waits for it to appear)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            out = subprocess.run(["pw-dump", "-N"], capture_output=True, text=True, timeout=3).stdout
            for obj in json.loads(out or "[]"):
                props = (obj.get("info") or {}).get("props") or {}
                if obj.get("type", "").endswith("Node") and props.get("node.name") == node_name:
                    return int(obj["id"])
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.15)


def set_controls(node_id: int, controls: dict[str, float]) -> bool:
    """Change filter-chain control values live, without restarting the graph."""
    params = " ".join(f"{json.dumps(k)} {float(v)!r}" for k, v in controls.items())
    try:
        res = subprocess.run(
            ["pw-cli", "set-param", str(node_id), "Props", f"{{ params = [ {params} ] }}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
