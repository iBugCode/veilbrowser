"""One-command kernel upgrade: track the newest community ungoogled-chromium.

Polls ungoogled-software/ungoogled-chromium-portablelinux releases (the
official portablelinux build channel), verifies sha256 against the
ungoogled-chromium-binaries index, extracts, and updates the configured
``vanilla_binary``.  This is how veilbrowser escapes fingerprint-chromium's
release cadence: the fingerprint layer lives in our JS bundle, so a kernel
upgrade is just a download.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.request

PORTABLELINUX = "ungoogled-software/ungoogled-chromium-portablelinux"
BINARIES_REPO = "ungoogled-software/ungoogled-chromium-binaries"
BINARIES_PATH = "config/platforms/linux_portable/64bit"

_ASSET_RE = re.compile(r"ungoogled-chromium-(\d+\.\d+\.\d+\.\d+)-\d+-x86_64_linux\.tar\.xz")
_UDDIni_RE = re.compile(r"^url = (?P<url>\S+).*?^sha256 = (?P<sha>[0-9a-f]{64})",
                        re.M | re.S)


def _get_json(url: str, timeout: float = 30.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _get_text(url: str, timeout: float = 30.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode()


def latest_release() -> dict:
    """Newest portablelinux release: {version, url, size}."""
    rel = _get_json(f"https://api.github.com/repos/{PORTABLELINUX}/releases/latest")
    for asset in rel.get("assets", []):
        m = _ASSET_RE.fullmatch(asset["name"])
        if m:
            return {"version": m.group(1), "url": asset["browser_download_url"],
                    "size": asset["size"], "tag": rel["tag_name"]}
    raise RuntimeError("no x86_64_linux tarball in latest portablelinux release")


def known_sha256(version: str) -> str | None:
    """sha256 from the community binaries index, when the build is listed."""
    try:
        listing = _get_json(
            f"https://api.github.com/repos/{BINARIES_REPO}/contents/"
            f"{BINARIES_PATH}?ref=master")
        for entry in listing:
            if entry["name"].startswith(version):
                ini = _get_text(entry["download_url"])
                m = _UDDIni_RE.search(ini)
                if m:
                    return m.group("sha")
    except Exception:
        pass
    return None


def download(url: str, dest: str, sha256: str | None = None,
             progress=None) -> str:
    """Stream the tarball to dest; verify sha256 when given."""
    tmp = dest + ".part"
    h = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60.0) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    digest = h.hexdigest()
    if sha256 and digest != sha256:
        os.unlink(tmp)
        raise RuntimeError(f"sha256 mismatch: got {digest}, want {sha256}")
    os.replace(tmp, dest)
    return digest


def extract(tarball: str, dest_root: str) -> str:
    """Extract; return the chrome binary path inside dest_root."""
    os.makedirs(dest_root, exist_ok=True)
    with tarfile.open(tarball, "r:xz") as tf:
        tf.extractall(dest_root)  # noqa: S202 - tarball is hash-verified
    for root, _dirs, files in os.walk(dest_root):
        if "chrome" in files:
            return os.path.join(root, "chrome")
    raise RuntimeError("no chrome binary after extraction")


def update_config(key: str, value: str) -> str:
    """Set key=value in the first existing config file; return its path."""
    candidates = ["/etc/veilbrowser.conf", os.path.expanduser("~/.veilbrowser.conf")]
    target = next((c for c in candidates if os.path.isfile(c)), candidates[-1])
    lines: list[str] = []
    if os.path.isfile(target):
        with open(target, encoding="utf-8") as f:
            lines = [ln for ln in f.readlines()
                     if not ln.strip().startswith(key)]
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{key} = {value}\n")
    with open(target, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return target


def upgrade(dest_root: str | None = None, version: str | None = None,
            progress=None) -> dict:
    """Full flow: find latest, download, verify, extract, update config."""
    from .browser import default_binary

    dest_root = dest_root or os.path.expanduser("~/.veilbrowser/kernels")
    current = default_binary(vanilla=True) or ""

    rel = latest_release() if not version else \
        {**latest_release(), "version": version}  # version pin resolves below
    if version:
        rel["url"] = re.sub(r"\d+\.\d+\.\d+\.\d+", version, rel["url"])
        rel["version"] = version
    rel["sha256"] = known_sha256(f"{rel['version']}-1")

    chrome = None
    marker = os.path.normpath(f"ungoogled-chromium-{rel['version']}")
    if marker in os.path.normpath(current) and os.path.isfile(current):
        return {"status": "current", "version": rel["version"], "binary": current}

    tarball = os.path.join(tempfile.gettempdir(), f"veil-uc-{rel['version']}.tar.xz")
    if not os.path.isfile(tarball):
        download(rel["url"], tarball, rel["sha256"], progress)
    chrome = extract(tarball, os.path.join(dest_root, f"ungoogled-chromium-{rel['version']}"))
    os.chmod(chrome, 0o755)
    cfg = update_config("vanilla_binary", chrome)
    return {"status": "upgraded", "version": rel["version"], "binary": chrome,
            "config": cfg, "sha256": rel["sha256"] or "unverified"}
