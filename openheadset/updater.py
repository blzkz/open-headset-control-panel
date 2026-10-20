"""Self-update from GitHub releases.

Detects how the app was installed and updates it the same way:

* AppImage            → atomically replaces the running AppImage file (no root);
* pacman / apt / dnf / zypper package → installs the release package via ``pkexec``;
* source checkout     → only reports that an update exists.

Every download is verified against the release's ``SHA256SUMS`` before use.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import subprocess
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import __version__
from .battery import CACHE_DIR

REPO = "blzkz/open-headset-control-panel"
API_HOST = "api.github.com"
PACKAGE = "open-headset"
PACKAGE_ROOT = Path("/usr/share/open-headset")
USER_AGENT = f"open-headset/{__version__}"


class UpdateError(Exception):
    pass


class InstallKind(Enum):
    APPIMAGE = "appimage"
    PACMAN = "pacman"
    DEB = "deb"
    RPM = "rpm"
    SOURCE = "source"

    @property
    def can_self_update(self) -> bool:
        return self is not InstallKind.SOURCE


ASSET_SUFFIX = {
    InstallKind.APPIMAGE: "-x86_64.AppImage",
    InstallKind.PACMAN: "-any.pkg.tar.zst",
    InstallKind.DEB: "_all.deb",
    InstallKind.RPM: ".noarch.rpm",
}


def parse_version(text: str) -> tuple[int, ...]:
    parts = []
    for chunk in text.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def _owned_by(cmd: list[str]) -> bool:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0 and PACKAGE in res.stdout


def detect_install() -> InstallKind:
    if os.environ.get("APPIMAGE"):
        return InstallKind.APPIMAGE
    here = Path(__file__).resolve()
    if PACKAGE_ROOT not in here.parents:
        return InstallKind.SOURCE
    probe = str(PACKAGE_ROOT / "openheadset" / "__init__.py")
    if shutil.which("pacman") and _owned_by(["pacman", "-Qqo", probe]):
        return InstallKind.PACMAN
    if shutil.which("dpkg") and _owned_by(["dpkg", "-S", probe]):
        return InstallKind.DEB
    if shutil.which("rpm") and _owned_by(["rpm", "-qf", probe]):
        return InstallKind.RPM
    return InstallKind.SOURCE


# -- GitHub ----------------------------------------------------------------------------------
def github_token() -> str | None:
    """A token is only needed while the repository is private (uses the user's gh login)."""
    for var in ("OPENHEADSET_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    if shutil.which("gh"):
        try:
            res = subprocess.run(["gh", "auth", "token", "--hostname", "github.com"],
                                 capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def _request(url: str, *, accept: str, token: str | None, timeout: float = 20.0,
             max_redirects: int = 5) -> http.client.HTTPResponse:
    """GET following redirects manually so the token never leaves api.github.com."""
    for _ in range(max_redirects + 1):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https":
            raise UpdateError(f"refusing non-HTTPS URL: {url}")
        conn = http.client.HTTPSConnection(parts.netloc, timeout=timeout)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        headers = {"User-Agent": USER_AGENT, "Accept": accept}
        if token and parts.netloc == API_HOST:
            headers["Authorization"] = f"Bearer {token}"
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        if resp.status in (301, 302, 303, 307, 308):
            url = resp.getheader("Location") or ""
            resp.close()
            continue
        if resp.status != 200:
            body = resp.read(300).decode(errors="replace")
            resp.close()
            raise UpdateError(f"GitHub HTTP {resp.status}: {body}")
        return resp
    raise UpdateError("too many redirects")


@dataclass(slots=True)
class Release:
    version: str
    tag: str
    notes: str
    html_url: str
    assets: dict[str, str] = field(default_factory=dict)  # name → API download URL

    def asset_for(self, kind: InstallKind) -> str | None:
        suffix = ASSET_SUFFIX.get(kind)
        return next((n for n in self.assets if suffix and n.endswith(suffix)), None)


def fetch_latest(token: str | None = None) -> Release:
    resp = _request(f"https://{API_HOST}/repos/{REPO}/releases/latest",
                    accept="application/vnd.github+json", token=token)
    with resp:
        data = json.loads(resp.read())
    return Release(
        version=str(data.get("tag_name", "")).lstrip("vV"),
        tag=str(data.get("tag_name", "")),
        notes=str(data.get("body") or ""),
        html_url=str(data.get("html_url") or f"https://github.com/{REPO}/releases"),
        assets={a["name"]: a["url"] for a in data.get("assets", []) if "name" in a and "url" in a},
    )


def _download(url: str, dest: Path, token: str | None, progress: Callable[[int], None] | None) -> str:
    """Stream to ``dest`` and return its SHA-256."""
    resp = _request(url, accept="application/octet-stream", token=token, timeout=60)
    total = int(resp.getheader("Content-Length") or 0)
    digest = hashlib.sha256()
    done = 0
    tmp = dest.with_suffix(dest.suffix + ".part")
    with resp, open(tmp, "wb") as fh:
        while chunk := resp.read(1 << 16):
            fh.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress and total:
                progress(min(100, done * 100 // total))
    os.replace(tmp, dest)
    return digest.hexdigest()


def download_verified(release: Release, kind: InstallKind, token: str | None = None,
                      progress: Callable[[int], None] | None = None) -> Path:
    name = release.asset_for(kind)
    if name is None:
        raise UpdateError(f"the release has no package for this installation ({kind.value})")
    if "SHA256SUMS" not in release.assets:
        raise UpdateError("the release has no SHA256SUMS: refusing to install an unverified file")
    sums_resp = _request(release.assets["SHA256SUMS"], accept="application/octet-stream", token=token)
    with sums_resp:
        sums = {}
        for line in sums_resp.read().decode().splitlines():
            parts = line.split()
            if len(parts) == 2:
                sums[parts[1].lstrip("*")] = parts[0].lower()
    expected = sums.get(name)
    if not expected:
        raise UpdateError(f"{name} is not listed in SHA256SUMS")
    folder = CACHE_DIR / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    actual = _download(release.assets[name], dest, token, progress)
    if actual != expected:
        dest.unlink(missing_ok=True)
        raise UpdateError(f"checksum mismatch for {name}")
    return dest


# -- installation ----------------------------------------------------------------------------
def install_command(kind: InstallKind, package: Path) -> list[str]:
    path = str(package.resolve())
    if kind is InstallKind.PACMAN:
        # --ask=4: answer "yes" to removing a conflicting package (the old openlogi-headset)
        return ["pkexec", "pacman", "-U", "--noconfirm", "--ask=4", path]
    if kind is InstallKind.DEB:
        if shutil.which("apt-get"):
            return ["pkexec", "apt-get", "install", "-y", "--allow-downgrades", path]
        return ["pkexec", "dpkg", "-i", path]
    if kind is InstallKind.RPM:
        if shutil.which("dnf"):
            return ["pkexec", "dnf", "install", "-y", path]
        if shutil.which("zypper"):
            return ["pkexec", "zypper", "--non-interactive", "install", "--allow-unsigned-rpm", path]
        return ["pkexec", "rpm", "-U", path]
    raise UpdateError(f"cannot install packages for {kind.value}")


def install(kind: InstallKind, package: Path) -> None:
    if kind is InstallKind.APPIMAGE:
        target = Path(os.environ["APPIMAGE"])
        tmp = target.with_name(target.name + ".new")
        try:
            shutil.copyfile(package, tmp)
            tmp.chmod(0o755)
            os.replace(tmp, target)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise UpdateError(f"cannot replace {target}: {exc.strerror or exc}") from exc
        return
    if shutil.which("pkexec") is None:
        raise UpdateError("pkexec not found")
    res = subprocess.run(install_command(kind, package), capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip().splitlines()[-3:]
        if res.returncode in (126, 127) and not detail:
            raise UpdateError("authorisation cancelled")
        raise UpdateError("installation failed: " + " ".join(detail))


def relaunch_command(kind: InstallKind) -> list[str]:
    if kind is InstallKind.APPIMAGE:
        return [os.environ["APPIMAGE"]]
    launcher = shutil.which("open-headset")
    return [launcher] if launcher else []
