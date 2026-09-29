"""Launch a fingerprint browser kernel with a FingerprintProfile, CloakBrowser-style.

Injection strategies: "js" injects our own fingerprint bundle into any kernel
via CDP (default — survives kernel upgrades on vanilla ungoogled-chromium),
"kernel" uses fingerprint-chromium's C++ patches, "both" stacks them, and
"native" runs the bundle that is compiled INTO the veil kernel
(kernel-patches/extra/veil) — zero external injection, parameters delivered
through the VEIL_PARAMS environment variable.
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


class _WorkerScopeInjector(threading.Thread):
    """Inject the fingerprint bundle into worker/ServiceWorker scopes.

    Page.addScriptToEvaluateOnNewDocument never runs inside workers, and the
    Worker-constructor wrapper can't reach SharedWorker/ServiceWorker scripts
    (a SW script must be a same-origin URL). So we auto-attach at the browser
    level with waitForDebuggerOnStart: every service worker pauses before its
    script executes, we evaluate the bundle into its fresh JS context, then
    let it run. Every other target type is resumed immediately.
    """

    daemon = True

    def __init__(self, browser_ws_url: str, script: str):
        super().__init__(name="veil-sw-injector")
        self._ws_url = browser_ws_url
        self._script = script
        self._stop = threading.Event()

    def run(self) -> None:
        import json as _json

        import websocket
        try:
            ws = websocket.create_connection(self._ws_url, timeout=5,
                                             suppress_origin=True)
        except Exception:
            return
        ws.settimeout(0.5)
        msg_id = [0]

        def call(method: str, params: dict | None = None, session: str | None = None) -> None:
            msg_id[0] += 1
            msg = {"id": msg_id[0], "method": method, "params": params or {}}
            if session:
                msg["sessionId"] = session
            try:
                ws.send(_json.dumps(msg))
            except Exception:
                pass

        call("Target.setAutoAttach",
             {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True})
        while not self._stop.is_set():
            try:
                event = _json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            if event.get("method") != "Target.attachedToTarget":
                continue
            session = event["params"]["sessionId"]
            ttype = event["params"]["targetInfo"]["type"]
            if ttype in ("service_worker", "shared_worker", "worker"):
                # No Runtime.enable: evaluate works without it and the enable
                # is a detectable automation signal (console serialization).
                call("Runtime.evaluate",
                     {"expression": self._script, "includeCommandLineAPI": False},
                     session=session)
            call("Runtime.runIfWaitingForDebugger", session=session)
        try:
            ws.close()
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()


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
    engine: str = "kernel"          # js | kernel | both | native
    kernel_version: str | None = None
    js_params: dict | None = None
    veil_params: dict | None = None   # native engine: params handed via env
    native_active: bool | None = None  # native engine: bundle confirmed running
    proxy_check: dict | None = None   # async exit-IP self-check result
    _forwarder: LocalForwarder | None = None
    _cleanup_dir: bool = False
    _own_procs: list = field(default_factory=list, repr=False)
    _worker_injector: _WorkerScopeInjector | None = None

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
        if self.js_params:
            page = self.devtools.new_page_cdp("about:blank")
            from .inject import install
            # kernel+overlay: only the canvas section runs; the kernel's own
            # UA/headers stay in charge, so no setUserAgentOverride here
            install(page, self.js_params, headers=self.engine != "kernel")
            # Always navigate: the target's *initial* about:blank document can
            # be swapped after registration, leaving it without the bundle —
            # only a fresh document is guaranteed to run it at document-start.
            page.navigate(url)
            return page
        return self.devtools.new_page_cdp(url)

    def stop(self) -> None:
        if self._worker_injector:
            self._worker_injector.stop()
            self._worker_injector = None
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


def _geo_align(profile: FingerprintProfile) -> FingerprintProfile:
    """Query the exit IP through the configured proxy (local forwarder for
    auth/socks5 upstreams, direct urllib for plain http) and fill unset
    timezone/language/geolocation/webrtc_ip. Fail-open: on any error the
    profile is untouched."""
    from .geo import align_profile, query_geo
    try:
        up = parse_proxy_url(profile.proxy)
    except Exception:
        return profile
    geo = None
    if up.has_auth or up.scheme != "http":
        fwd = LocalForwarder(up)
        try:
            geo = query_geo(local_port=fwd.local_port)
        finally:
            fwd.stop()
    else:
        geo = query_geo(proxy_url=f"http://{up.host}:{up.port}")
    return align_profile(profile, geo) if geo else profile


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


def _close_session_page(browser: "Browser", page: CDP) -> None:
    """Close a target opened only for a background check."""
    try:
        info = page.call("Target.getTargetInfo")
        browser.devtools.close_page(info["targetInfo"]["targetId"])
    except Exception:
        pass


def _verify_proxy_async(b: "Browser", expected_ip: str) -> None:
    """Fetch the exit IP through the browser's own network stack in the
    background; a mismatch with the externally measured proxy exit means
    Chrome is silently bypassing the proxy (CloakBrowser #157: authenticated
    SOCKS5 falling back to a direct connection)."""
    from .probe import check_exit_ip

    def run() -> None:
        try:
            page = b.devtools.new_page_cdp("about:blank")
            try:
                b.proxy_check = check_exit_ip(page, expected_ip)
            finally:
                _close_session_page(b, page)
        except Exception as exc:  # informational only — never break launch
            b.proxy_check = {"error": str(exc), "expected": expected_ip}

    threading.Thread(target=run, daemon=True, name="veil-proxy-check").start()


def _verify_native_async(b: "Browser") -> None:
    """Confirm the compiled-in bundle actually runs (an unpatched kernel
    silently ignores VEIL_PARAMS — surface that as native_active=False)."""
    def run() -> None:
        try:
            page = b.devtools.new_page_cdp("about:blank")
            try:
                b.native_active = bool(
                    page.evaluate("typeof veilNativeCfg === 'function'"))
            finally:
                _close_session_page(b, page)
        except Exception:
            b.native_active = False

    threading.Thread(target=run, daemon=True, name="veil-native-check").start()


def launch(profile: FingerprintProfile | None = None,
           *,
           engine: str = "js",
           headless: bool = True,
           binary: str | None = None,
           user_data_dir: str | None = None,
           software_webgl: bool | None = None,
           extra_flags: list[str] | None = None,
           start_timeout: float = 20.0,
           js_overlay: bool = False) -> Browser:
    """Start the browser. Blocks until the DevTools endpoint is up.

    engine selects where the fingerprint lives:
      * "js"     — our injected bundle on any kernel (default; kernel-upgrade
                   proof, works on vanilla ungoogled-chromium)
      * "kernel" — fingerprint-chromium's C++ patches
      * "both"   — kernel patches + our bundle on top
      * "native" — the bundle compiled into the veil kernel; no CDP, no
                   injection — requires a binary built with
                   kernel-patches/extra/veil (scripts/build-kernel.sh)

    With engine="kernel", ``js_overlay=True`` additionally injects the JS
    bundle's canvas section only — fingerprint-chromium leaves pure-text
    canvas readbacks unnoised; the overlay closes that without touching the
    kernel-owned identity.

    A proxy with credentials on the profile is transparently wrapped in a
    local authenticated forwarder (--proxy-server cannot auth).
    """
    if engine not in ("js", "kernel", "both", "native"):
        raise ValueError(f"unknown engine: {engine!r}")
    if engine == "native" and js_overlay:
        raise ValueError("engine='native' has nothing to overlay: the bundle "
                         "is compiled into the kernel")
    profile = profile or FingerprintProfile(seed=0)
    kernel_fp = engine in ("kernel", "both", "native")
    binary = binary or default_binary(vanilla=engine == "js")
    if not binary:
        raise RuntimeError(
            "kernel binary not found; set VEIL_CHROME_PATH / VEIL_VANILLA_CHROME_PATH "
            "or pass binary=")
    if not os.access(binary, os.X_OK):
        raise RuntimeError(f"binary not executable: {binary}")

    udd = user_data_dir or tempfile.mkdtemp(prefix="veil-")

    # GeoIP alignment must run before resolved(): the seed then fills only
    # what the exit IP didn't already determine. Also resolves geolocation
    # and the WebRTC exit IP; explicit values always win.
    if profile.proxy and (profile.timezone is None or profile.language is None
                          or profile.geolocation is None
                          or profile.webrtc_ip is None):
        profile = _geo_align(profile)
    profile = profile.resolved()
    if profile.proxy:
        _seed_webrtc_prefs(udd)

    js_params = None
    if engine in ("js", "both") or (engine == "kernel" and js_overlay):
        from .inject import js_params as make_js_params
        # kernel version from the binary path (DevTools reporting it would be
        # too late — the --user-agent switch below must be in the first spawn)
        mver = re.search(r"(\d+\.\d+\.\d+\.\d+)", binary)
        chrome_full = profile.brand_version or (mver.group(1) if mver else None)
        js_params = make_js_params(profile, chrome_full)
        if engine == "kernel":  # overlay mode: canvas section only
            js_params["spoof"] = {k: False for k in js_params["spoof"]}
            js_params["spoof"]["canvas"] = True

    veil_params: dict | None = None
    if engine == "native":
        # The kernel's compiled-in bundle reads its parameters from the
        # VEIL_PARAMS environment variable; metric-clone fonts are baked into
        # the binary, so they are stripped from the payload.
        from .inject import js_params as make_js_params
        mver = re.search(r"(\d+\.\d+\.\d+\.\d+)", binary)
        chrome_full = profile.brand_version or (mver.group(1) if mver else None)
        if chrome_full is None:
            try:  # the kernel's own version keeps the UA stories consistent
                out = subprocess.run([binary, "--version"], capture_output=True,
                                     text=True, timeout=15).stdout or ""
                vm = re.search(r"(\d+\.\d+\.\d+\.\d+)", out)
                chrome_full = vm.group(1) if vm else None
            except Exception:
                chrome_full = None
        veil_params = make_js_params(profile, chrome_full)
        veil_params.pop("fontData", None)

    flags = [f"--user-data-dir={udd}",
             "--remote-debugging-port=0", "--no-first-run", "--no-default-browser-check",
             "--disable-sync", "--disable-features=Translate"]
    flags += profile.fingerprint_flags(kernel_fp=kernel_fp)
    if js_params and engine != "kernel":
        # Process-wide HTTP User-Agent: CDP's setUserAgentOverride only covers
        # the attached page session, so worker/subframe fetches would otherwise
        # leak the real (e.g. HeadlessChrome) UA on the wire.
        flags.append(f"--user-agent={js_params['userAgent']}")
        # Vanilla Chromium honours --timezone process-wide (service workers
        # included) — our JS tz hook only reaches window scopes.
        if profile.timezone:
            flags.append(f"--timezone={profile.timezone}")
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

    env: dict | None = None
    if veil_params is not None:
        import json as _json
        env = dict(os.environ)
        env["VEIL_PARAMS"] = _json.dumps(veil_params, separators=(",", ":"),
                                         sort_keys=True)
    proc = subprocess.Popen([binary] + flags,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, env=env)

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
                engine=engine, kernel_version=kernel_version, js_params=js_params,
                veil_params=veil_params)
    if engine == "native":
        _verify_native_async(b)
    if profile.proxy and profile.webrtc_ip:
        _verify_proxy_async(b, profile.webrtc_ip)
    if js_params:
        try:
            from .inject import build_script
            bws = str(devtools.version().get("webSocketDebuggerUrl") or "")
            if bws:
                b._worker_injector = _WorkerScopeInjector(bws, build_script(js_params))
                b._worker_injector.start()
        except Exception:
            b._worker_injector = None
    b._forwarder = forwarder
    b._cleanup_dir = user_data_dir is None
    atexit.register(b.stop)
    return b


# Convenience mirroring CloakBrowser's sync API shape
def start(**kwargs) -> Browser:
    return launch(**kwargs)
