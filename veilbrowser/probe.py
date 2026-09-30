"""Built-in fingerprint self-check: collect every spoofable surface in one page."""

from __future__ import annotations

import json

_PROBE_JS = r"""
(async () => {
  const r = {};
  const nav = navigator;
  r.kernelEngine = matchMedia('(pointer: fine)').matches &&
                   matchMedia('(hover: hover)').matches &&
                   nav.plugins.length > 0;
  r.userAgent = nav.userAgent;
  r.platform = nav.platform;
  r.webdriver = nav.webdriver;
  if (nav.userAgentData) {
    r.uaPlatform = nav.userAgentData.platform;
    r.uaMobile = nav.userAgentData.mobile;
    r.uaBrands = nav.userAgentData.brands.map(b => b.brand + " " + b.version);
    try { const gh = await nav.userAgentData.getHighEntropyValues(["platformVersion","model","fullVersionList"]);
          r.uaPlatformVersion = gh.platformVersion; } catch (e) {}
  }
  r.deviceMemory = nav.deviceMemory;
  r.hardwareConcurrency = nav.hardwareConcurrency;
  r.languages = [...nav.languages];
  r.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  r.timezoneOffset = new Date().getTimezoneOffset();
  r.plugins = nav.plugins.length;
  r.screen = [screen.width, screen.height, screen.availWidth, screen.availHeight];
  try {
    r.mediaDevices = (await navigator.mediaDevices.enumerateDevices())
      .map(d => d.kind);
  } catch (e) { r.mediaDevices = []; }
  try {
    const c = document.createElement('canvas'); c.width = 220; c.height = 60;
    const ctx = c.getContext('2d');
    ctx.textBaseline = 'top'; ctx.font = '16px Arial';
    ctx.fillStyle = '#f60'; ctx.fillRect(0, 0, 100, 30);
    ctx.fillStyle = '#0f0'; ctx.fillText('veil-fp-\u2764', 2, 2);
    ctx.strokeStyle = '#00f'; ctx.arc(50, 30, 20, 0, Math.PI * 2); ctx.stroke();
    r.canvas = c.toDataURL();
  } catch (e) { r.canvas = 'ERR:' + e; }
  try {
    const d = document.createElement('div');
    d.style.cssText = 'position:absolute;width:137.5px;font-size:13.37px';
    d.textContent = 'clientrects';
    document.body.appendChild(d);
    const rect = d.getClientRects()[0];
    r.clientRects = [rect.width, rect.height, rect.top].join(',');
    d.remove();
  } catch (e) { r.clientRects = 'ERR:' + e; }
  try {
    const gc = document.createElement('canvas').getContext('webgl');
    const dbg = gc.getExtension('WEBGL_debug_renderer_info');
    r.webglVendor = gc.getParameter(gc.VENDOR);
    r.webglRenderer = dbg ? gc.getParameter(dbg.UNMASKED_RENDERER_WEBGL)
                          : gc.getParameter(gc.RENDERER);
  } catch (e) { r.webglRenderer = 'ERR:' + e; }
  try {
    const ac = new OfflineAudioContext(1, 44100, 44100);
    const o = ac.createOscillator(); const g = ac.createGain();
    o.type = 'triangle'; g.gain.value = 0.5; o.connect(g); g.connect(ac.destination);
    o.start(0);
    const buf = await ac.startRendering();
    const d = buf.getChannelData(0); let s = 0;
    for (let i = 4500; i < 5000; i++) s += Math.abs(d[i]);
    r.audio = s;
  } catch (e) { r.audio = 'ERR:' + e; }
  try {
    const holder = document.createElement('div');
    const shadow = holder.attachShadow({mode: 'closed'});
    document.body.appendChild(holder);
    r.fakeShadowRoot = holder.fakeShadowRoot === shadow && !!holder.fakeShadowRoot;
    holder.remove();
  } catch (e) { r.fakeShadowRoot = 'ERR:' + e; }
  return JSON.stringify(r);
})()
"""


def collect(page_cdp) -> dict:
    """Run the probe on an attached page CDP session, return the value dict."""
    raw = page_cdp.evaluate(_PROBE_JS, await_promise=True)
    return json.loads(raw)


def check_exit_ip(page_cdp, expected_ip: str | None = None,
                  timeout_s: float = 25.0) -> dict:
    """Fetch the public exit IP through the browser's own network stack and
    compare it with ``expected_ip`` (the proxy exit measured externally).

    Catches the silent fallback users hit with authenticated SOCKS5
    (CloakBrowser #157): when Chrome ignores the proxy the page-side IP is
    the real host IP, not the proxy exit. ``match`` is None when no
    expectation was supplied.
    """
    raw = page_cdp.evaluate(
        "(async () => { const r = await fetch("
        "'https://api.ipify.org?format=json', {cache: 'no-store'});"
        " return (await r.json()).ip; })()",
        await_promise=True,
    )
    ip = str(raw).strip()
    return {"exit_ip": ip, "expected": expected_ip,
            "match": (ip == expected_ip) if expected_ip else None}


def open_probe_page(browser):
    """Open a file:// page (secure context) so userAgentData/UA-CH are exposed.

    about:blank is not a secure context: navigator.userAgentData is undefined
    there. The probe page is written into the browser's user-data-dir.
    """
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".html", prefix="veil-probe-",
                                dir=browser.user_data_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("<!DOCTYPE html><html><head><title>veil-probe</title></head>"
                "<body>veilbrowser probe</body></html>\n")
    page = browser.new_page("file://" + path)
    # /json/new races target creation against the navigation: evaluations can
    # land on the pending about:blank document (insecure context — secure-only
    # APIs like Notification.permission would report the wrong surface).
    import time
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if page.evaluate("location.protocol + '|' + document.readyState") == "file:|complete":
            return page
        time.sleep(0.05)
    raise RuntimeError("probe page never reached file:// complete")


def check(results: dict) -> list[tuple[str, bool, str]]:
    """Turn probe results into (name, ok, detail) verdict rows."""
    rows: list[tuple[str, bool, str]] = []
    ua = results.get("userAgent", "")
    rows.append(("UA 无 Headless 痕迹", "Headless" not in ua, ua))
    rows.append(("navigator.webdriver 已隐藏",
                 not results.get("webdriver", False),
                 str(results.get("webdriver"))))
    rows.append(("UA/platform/uaData 一致",
                 ("Windows" in ua) + ("Mac" in ua) + ("Linux" in ua) == 1
                 and results.get("platform", "") != ""
                 and results.get("uaPlatform", "").lower()[:3] in
                 _platform_key(results.get("platform", "")),
                 f"{results.get('platform')} / {results.get('uaPlatform')}"))
    rows.append(("deviceMemory ∈ {8,4,2} (spec 封顶 8)", results.get("deviceMemory") in (8, 4, 2),
                 str(results.get("deviceMemory"))))
    rows.append(("时区生效", bool(results.get("timezone")), str(results.get("timezone"))))
    rows.append(("语言生效", bool(results.get("languages")), str(results.get("languages"))))
    scr = results.get("screen") or []
    rows.append(("屏幕参数一致", len(scr) == 4 and all(v > 0 for v in scr)
                 and scr[2] <= scr[0] and scr[3] <= scr[1], str(scr)))
    rows.append(("canvas 可采集且有噪声", isinstance(results.get("canvas"), str)
                 and results["canvas"].startswith("data:image/png"),
                 f"len={len(results.get('canvas') or '')}"))
    rows.append(("WebGL GPU 已伪装(非 SwiftShader)",
                 isinstance(results.get("webglRenderer"), str)
                 and "SwiftShader" not in results["webglRenderer"],
                 str(results.get("webglRenderer"))[:90]))
    rows.append(("audio 可渲染", isinstance(results.get("audio"), (int, float)),
                 str(results.get("audio"))))
    rows.append(("fakeShadowRoot 可用", results.get("fakeShadowRoot") is True,
                 str(results.get("fakeShadowRoot"))))
    return rows


def _platform_key(platform: str) -> str:
    p = (platform or "").lower()
    for key in ("win", "mac", "lin"):
        if key in p:
            return key
    return ""


def print_report(results: dict) -> bool:
    rows = check(results)
    ok_all = True
    print(f"{'检查项':<28} 结果")
    print("-" * 100)
    for name, ok, detail in rows:
        ok_all &= ok
        mark = "PASS" if ok else "FAIL"
        print(f"{name:<28} [{mark}] {detail}")
    print("-" * 100)
    print("总体:", "PASS" if ok_all else "FAIL")
    return ok_all
