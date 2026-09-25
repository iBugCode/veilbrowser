"""Launch a fingerprint browser kernel with a FingerprintProfile, CloakBrowser-style.

Two engines: "js" injects our own fingerprint bundle into any kernel via CDP
(default — survives kernel upgrades on vanilla ungoogled-chromium), "kernel"
uses fingerprint-chromium's C++ patches, "both" stacks them.
"""

from __future__ import annotations

import atexit
import glob
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field

from .cdp import CDP, DevTools
from .profile import FingerprintProfile
from .proxy import LocalForwarder, UpstreamProxy, parse_proxy_url

_DEVTOOLS_RE = re.compile(r"DevTools listening on (ws://\S+)")


_CONFIG_CANDIDATES = ("/etc/veilbrowser.conf", "~/.veilbrowser.conf")


def _binary_from_config(key: str = "binary") -> str | None:
    for cfg in _CONFIG_CANDIDATES:
        path = os.path.expanduser(cfg)
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.split("=")[0].strip() == key:
                        val = line.split("=", 1)[1].strip().strip('"')
                        if val and os.path.isfile(val):
                            return val
        except OSError:
            continue
    return None


def default_binary(vanilla: bool = False) -> str | None:
    """Find a kernel binary: env var, PATH, config, well-known dirs.

    vanilla=True looks for an unpatched ungoogled-chromium portablelinux
    build (used with engine="js"); default looks for a fingerprint-chromium
    kernel (engine="kernel"/"both").  A vanilla binary works with every
    engine, so callers can always fall back to it.
    """
    env = os.environ.get("VEIL_VANILLA_CHROME_PATH" if vanilla else "VEIL_CHROME_PATH")
    if env and os.path.isfile(env):
        return env
    if not vanilla:
        found = shutil.which("fingerprint-chromium") or shutil.which("cloak-chromium")
        if found:
            return found
    from_config = _binary_from_config("vanilla_binary" if vanilla else "binary")
    if from_config:
        return from_config
    if vanilla:
        patterns = [
            "/home/*/ungoogled-chromium-1*/ungoogled-chromium-*-x86_64_linux/chrome",
            "/root/*/ungoogled-chromium-1*/ungoogled-chromium-*-x86_64_linux/chrome",
            "/opt/ungoogled-chromium*/chrome",
        ]
    else:
        patterns = [
            "/home/*/fingerprint-chromium-poc/ungoogled-chromium-*-linux/chrome",
            "/home/*/fingerprint-chromium*/ungoogled-chromium-*-linux/chrome",
            "/root/*/fingerprint-chromium-poc/ungoogled-chromium-*-linux/chrome",
            "/opt/fingerprint-chromium*/chrome",
            "/usr/local/fingerprint-chromium*/chrome",
        ]
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


@dataclass
class Browser:
    profile: FingerprintProfile
    binary: str
    proc: subprocess.Popen
    devtools: DevTools
    user_data_dir: str
    proxy_arg: str | None = None
    engine: str = "kernel"          # js | kernel | both
    kernel_version: str | None = None
    js_params: dict | None = None
    _forwarder: LocalForwarder | None = None
    _cleanup_dir: bool = False
    _own_procs: list = field(default_factory=list, repr=False)

    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def port(self) -> int:
        return self.devtools.port

    def cdp_browser(self) -> CDP:
        """Attach to the browser-level DevTools websocket."""
        ws = self.devtools.version().get("webSocketDebuggerUrl")
        if not ws:
            raise RuntimeError("browser has no webSocketDebuggerUrl (already attached?)")
        return self.devtools.attach(ws)

    def new_page(self, url: str = "about:blank") -> CDP:
        """Open a page target and return an attached CDP session to it.

        With the JS engine active the fingerprint bundle is installed before
        the first navigation, so it runs at document-start on every frame.
        """
        if self.engine in ("js", "both"):
            page = self.devtools.new_page_cdp("about:blank")
            from .inject import install
            install(page, self.js_params)
            # Always navigate: the target's *initial* about:blank document can
            # be swapped after registration, leaving it without the bundle —
            # only a fresh document is guaranteed to run it at document-start.
            page.navigate(url)
            return page
        return self.devtools.new_page_cdp(url)

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self._forwarder:
            self._forwarder.stop()
            self._forwarder = None
        if self._cleanup_dir:
            shutil.rmtree(self.user_data_dir, ignore_errors=True)
            self._cleanup_dir = False

    def __enter__(self) -> "Browser":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def _seed_webrtc_prefs(user_data_dir: str) -> None:
    """Disable non-proxied WebRTC before first launch so the real IP cannot
    leak around the proxy (Camoufox-style IP handling, pref-level)."""
    import json
    ddir = os.path.join(user_data_dir, "Default")
    os.makedirs(ddir, exist_ok=True)
    prefs_path = os.path.join(ddir, "Preferences")
    prefs: dict = {}
    if os.path.isfile(prefs_path):
        try:
            with open(prefs_path, encoding="utf-8") as f:
                prefs = json.load(f)
        except (OSError, ValueError):
            prefs = {}
    prefs.setdefault("webrtc", {})
    prefs["webrtc"]["ip_handling_policy"] = "disable_non_proxied_udp"
    prefs["webrtc"]["multiple_routes_enabled"] = False
    prefs["webrtc"]["nonproxied_udp_enabled"] = False
    with open(prefs_path, "w", encoding="utf-8") as f:
        json.dump(prefs, f)


def launch(profile: FingerprintProfile | None = None,
           *,
           engine: str = "js",
           headless: bool = True,
           binary: str | None = None,
           user_data_dir: str | None = None,
           software_webgl: bool | None = None,
           extra_flags: list[str] | None = None,
           start_timeout: float = 20.0) -> Browser:
    """Start the browser. Blocks until the DevTools endpoint is up.

    engine selects where the fingerprint lives:
      * "js"     — our injected bundle on any kernel (default; kernel-upgrade
                   proof, works on vanilla ungoogled-chromium)
      * "kernel" — fingerprint-chromium's C++ patches
      * "both"   — kernel patches + our bundle on top

    A proxy with credentials on the profile is transparently wrapped in a
    local authenticated forwarder (--proxy-server cannot auth).
    """
    if engine not in ("js", "kernel", "both"):
        raise ValueError(f"unknown engine: {engine!r}")
    profile = (profile or FingerprintProfile(seed=0)).resolved()
    kernel_fp = engine in ("kernel", "both")
    binary = binary or default_binary(vanilla=engine == "js")
    if not binary:
        raise RuntimeError(
            "kernel binary not found; set VEIL_CHROME_PATH / VEIL_VANILLA_CHROME_PATH "
            "or pass binary=")
    if not os.access(binary, os.X_OK):
        raise RuntimeError(f"binary not executable: {binary}")

    udd = user_data_dir or tempfile.mkdtemp(prefix="veil-")
    if profile.proxy:
        _seed_webrtc_prefs(udd)

    js_params = None
    if engine in ("js", "both"):
        from .inject import js_params
        # kernel version from the binary path (DevTools reporting it would be
        # too late — the --user-agent switch below must be in the first spawn)
        mver = re.search(r"(\d+\.\d+\.\d+\.\d+)", binary)
        chrome_full = profile.brand_version or (mver.group(1) if mver else None)
        js_params = js_params(profile, chrome_full)

    flags = [f"--user-data-dir={udd}",
             "--remote-debugging-port=0", "--no-first-run", "--no-default-browser-check",
             "--disable-sync", "--disable-features=Translate"]
    flags += profile.fingerprint_flags(kernel_fp=kernel_fp)
    if js_params:
        # Process-wide HTTP User-Agent: CDP's setUserAgentOverride only covers
        # the attached page session, so worker/subframe fetches would otherwise
        # leak the real (e.g. HeadlessChrome) UA on the wire.
        flags.append(f"--user-agent={js_params['userAgent']}")
    flags += list(extra_flags or [])

    if os.geteuid() == 0:  # root: sandbox is unsupported
        flags.append("--no-sandbox")
    if headless:
        flags.append("--headless=new")
        if software_webgl is None:
            software_webgl = True
        if software_webgl:
            flags += ["--use-gl=angle", "--use-angle=swiftshader", "--disable-dev-shm-usage"]
    elif software_webgl:
        flags += ["--use-gl=angle", "--use-angle=swiftshader"]

    forwarder: LocalForwarder | None = None
    proxy_arg: str | None = None
    if profile.proxy:
        up = parse_proxy_url(profile.proxy)
        if up.has_auth:
            forwarder = LocalForwarder(up)
            proxy_arg = forwarder.chrome_proxy_arg()
        else:
            proxy_arg = f"{up.scheme}://{up.host}:{up.port}"
        flags.append(f"--proxy-server={proxy_arg}")

    proc = subprocess.Popen([binary] + flags,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True)

    ws_url_holder: list[str] = []
    stderr_lines: list[str] = []

    def _read_stderr() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            stderr_lines.append(line)
            m = _DEVTOOLS_RE.search(line)
            if m:
                ws_url_holder.append(m.group(1))
                return  # stop consuming; remaining stderr buffered by OS pipe

    reader = threading.Thread(target=_read_stderr, daemon=True)
    reader.start()

    deadline = time.monotonic() + start_timeout
    while time.monotonic() < deadline:
        if ws_url_holder:
            break
        if proc.poll() is not None:
            raise RuntimeError(
                f"browser exited with {proc.returncode}\nstderr:\n{''.join(stderr_lines[-30:])}")
        time.sleep(0.05)
    if not ws_url_holder:
        proc.kill()
        raise RuntimeError(
            f"no DevTools endpoint within {start_timeout}s\nstderr:\n{''.join(stderr_lines[-30:])}")

    m = re.search(r"ws://[^:]+:(\d+)/", ws_url_holder[0])
    port = int(m.group(1))
    devtools = DevTools(port)
    kernel_version: str | None = None
    try:
        bver = str(devtools.version().get("Browser") or "")
        mv = re.search(r"Chrome/(\d+\.\d+\.\d+\.\d+)", bver)
        if mv:
            kernel_version = mv.group(1)
    except Exception:
        pass
    b = Browser(profile=profile, binary=binary, proc=proc,
                devtools=devtools,
                user_data_dir=flags[0].split("=", 1)[1],
                proxy_arg=proxy_arg,
                engine=engine, kernel_version=kernel_version, js_params=js_params)
    b._forwarder = forwarder
    b._cleanup_dir = user_data_dir is None
    atexit.register(b.stop)
    return b


# Convenience mirroring CloakBrowser's sync API shape
def start(**kwargs) -> Browser:
    return launch(**kwargs)
