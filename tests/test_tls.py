"""TLS fingerprint alignment: the patched kernel must produce the exact
same ClientHello (ja3) as the stock ungoogled binary — the patch layer
never touches the network stack, and now a test proves it.
"""

from __future__ import annotations

import os

import pytest

import veilbrowser
from veilbrowser.tls import TLSProbeServer


def _probe(binary: str, port: int, profile=None):
    b = veilbrowser.launch(profile, binary=binary, headless=True,
                           extra_flags=[
                               f"--ignore-certificate-errors",
                               f"--origin-to-force-quic=never",
                               "--disable-features=EncryptedClientHello",
                           ])
    try:
        page = b.new_page(f"https://127.0.0.1:{port}/")
    finally:
        pass
    return b


BUILT_CHROME = "/home/Project/build/src/out/Default/chrome"


def test_client_hello_matches_stock(vanilla_path):
    """The self-built veil kernel's ClientHello (normalized ja3) must equal
    the stock binary's — the patch layer never touches the network stack."""
    if not os.path.isfile(BUILT_CHROME):
        pytest.skip("self-built veil-chromium not available yet")
    results = {}
    for name, binary in (("stock", vanilla_path), ("veil", BUILT_CHROME)):
        srv = TLSProbeServer()
        b = None
        try:
            b = _probe(binary, srv.port)
            hellos = srv.wait_for(1)
            assert hellos, f"{name}: no ClientHello captured"
            results[name] = hellos[0]
        finally:
            if b:
                b.stop()
            srv.stop()
    stock, veil = results["stock"], results["veil"]
    assert stock.has_grease and veil.has_grease, "GREASE missing — not Chrome-like"
    assert veil.ja3_normalized == stock.ja3_normalized, (
        f"handshake diverged:\nstock: {stock.ja3_string}\nveil:  {veil.ja3_string}")


def test_client_hello_parses_unit():
    from veilbrowser.tls import parse_client_hello
    # minimal synthetic TLS record with a ClientHello
    import struct
    ciphers = b"\x13\x01\x13\x02\xc0\x2f"
    exts = (struct.pack(">HH", 0x000A, 6) + struct.pack(">H", 4) +
            b"\x00\x1d\x00\x17" +
            struct.pack(">HH", 0x000B, 2) + b"\x01\x00")
    body = (b"\x03\x03" + b"\x11" * 32 + b"\x00" + struct.pack(">H", len(ciphers)) +
            ciphers + b"\x01\x00" + struct.pack(">H", len(exts)) + exts)
    record = b"\x16\x03\x01" + struct.pack(">H", len(body) + 4) + b"\x01" + \
        struct.pack(">I", len(body))[1:] + body
    hello = parse_client_hello(record)
    assert hello is not None
    assert hello.version == "771"
    assert hello.ciphers == ["4865", "4866", "49199"]
    assert hello.extensions == ["10", "11"]
    assert hello.curves == ["29", "23"]
    assert hello.point_formats == ["0"]
    assert hello.has_grease is False


def test_console_getter_probe_stays_silent(js_probe_page):
    """Runtime.enable makes console.log serialize arguments, invoking getters
    — a published DevTools-detection trick. Our sessions never enable the
    runtime, so the probe must stay silent."""
    from veilbrowser.profile import FingerprintProfile
    prof = FingerprintProfile(seed=501).resolved()
    b, page = js_probe_page(prof)
    page.evaluate("""
      window.__probed = false;
      const evil = { get log() { window.__probed = true; return 1; } };
      console.log(evil);
      console.log('plain');
    """)
    import time
    deadline = time.monotonic() + 0.6
    probed = True
    while time.monotonic() < deadline:
        probed = page.evaluate("window.__probed")
        if probed:
            break
        time.sleep(0.1)
    assert probed is False, "console.log serialized its argument — CDP runtime enabled"
