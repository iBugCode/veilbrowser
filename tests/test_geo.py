"""Proxy-exit GeoIP alignment: timezone/language derived from the proxy's
exit IP, queried through the same chain the browser will use."""

from __future__ import annotations

import http.server
import json
import socket
import threading

import pytest

from veilbrowser.geo import align_profile, locale_for_country, query_geo
from veilbrowser.profile import FingerprintProfile
from veilbrowser.proxy import LocalForwarder, UpstreamProxy, parse_proxy_url

_GEO_JSON = json.dumps({"status": "success", "country": "Japan",
                        "countryCode": "JP", "timezone": "Asia/Tokyo",
                        "lat": 35.6895, "lon": 139.6917,
                        "query": "203.0.113.7"}).encode()


class _FakeOrigin(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_GEO_JSON)))
        self.end_headers()
        self.wfile.write(_GEO_JSON)

    def log_message(self, *a):
        pass


class _FakeProxy:
    """Speaks just enough upstream-proxy for the tests: tunnels CONNECT to
    the fake origin, answers absolute-form GET directly with the JSON."""

    def __init__(self, origin_port: int):
        self._origin = origin_port
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(8)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self._srv.settimeout(0.3)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(4096)
                if not chunk:
                    conn.close()
                    return
                head += chunk
            if head.startswith(b"CONNECT"):
                conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                remote = socket.create_connection(("127.0.0.1", self._origin))
                threading.Thread(target=self._pump, args=(conn, remote), daemon=True).start()
                self._pump(remote, conn)
            else:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                             b"Connection: close\r\nContent-Length: "
                             + str(len(_GEO_JSON)).encode() + b"\r\n\r\n" + _GEO_JSON)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _pump(a, b):
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except OSError:
            pass

    def stop(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


@pytest.fixture()
def fake_geo_chain():
    origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeOrigin)
    threading.Thread(target=origin.serve_forever, daemon=True).start()
    proxy = _FakeProxy(origin.server_address[1])
    yield proxy
    proxy.stop()
    origin.shutdown()


def test_locale_for_country():
    assert locale_for_country("JP") == "ja-JP"
    assert locale_for_country("br") == "pt-BR"
    assert locale_for_country("ZZ") == "en-US"
    assert locale_for_country(None) == "en-US"


def test_align_profile_fills_only_unset():
    p = FingerprintProfile(seed=1, proxy="socks5://u:p@1.2.3.4:1080")
    a = align_profile(p, {"countryCode": "JP", "timezone": "Asia/Tokyo"})
    assert a.timezone == "Asia/Tokyo"
    assert a.language == "ja-JP"

    explicit = align_profile(
        FingerprintProfile(seed=1, proxy="socks5://u:p@1.2.3.4:1080",
                           timezone="Europe/Berlin", language="fr-FR"),
        {"countryCode": "JP", "timezone": "Asia/Tokyo"})
    assert explicit.timezone == "Europe/Berlin"
    assert explicit.language == "fr-FR"


def test_query_geo_through_local_forwarder(fake_geo_chain):
    up = parse_proxy_url(f"http://127.0.0.1:{fake_geo_chain.port}")
    fwd = LocalForwarder(up)
    try:
        geo = query_geo(local_port=fwd.local_port)
    finally:
        fwd.stop()
    assert geo and geo["timezone"] == "Asia/Tokyo" and geo["countryCode"] == "JP"


def test_query_geo_direct_http(fake_geo_chain):
    geo = query_geo(proxy_url=f"http://127.0.0.1:{fake_geo_chain.port}")
    assert geo and geo["countryCode"] == "JP"


def test_query_geo_fail_open():
    # nothing listens here -> None, never an exception
    assert query_geo(proxy_url="http://127.0.0.1:1", timeout=0.5) is None


def test_launch_geo_aligns_timezone_and_language(fake_geo_chain, vanilla_path):
    from veilbrowser import launch
    p = FingerprintProfile(seed=123, platform="windows",
                           proxy=f"http://127.0.0.1:{fake_geo_chain.port}")
    b = launch(p, engine="js", binary=vanilla_path)
    try:
        page = b.new_page("about:blank")
        r = page.evaluate(
            "[Intl.DateTimeFormat().resolvedOptions().timeZone, navigator.language]")
        assert r == ["Asia/Tokyo", "ja-JP"]
    finally:
        b.stop()


def test_launch_explicit_profile_beats_geo(fake_geo_chain, vanilla_path):
    from veilbrowser import launch
    p = FingerprintProfile(seed=124, platform="windows", language="en-US",
                           timezone="America/New_York",
                           proxy=f"http://127.0.0.1:{fake_geo_chain.port}")
    b = launch(p, engine="js", binary=vanilla_path)
    try:
        page = b.new_page("about:blank")
        r = page.evaluate(
            "[Intl.DateTimeFormat().resolvedOptions().timeZone, navigator.language]")
        assert r == ["America/New_York", "en-US"]
    finally:
        b.stop()


def test_launch_fills_geolocation_and_webrtc_ip(fake_geo_chain, vanilla_path):
    from veilbrowser import launch
    p = FingerprintProfile(seed=125, platform="windows",
                           proxy=f"http://127.0.0.1:{fake_geo_chain.port}")
    b = launch(p, engine="js", binary=vanilla_path)
    try:
        assert b.profile.webrtc_ip == "203.0.113.7"
        assert b.profile.geolocation == (35.6895, 139.6917)
        page = b.new_page("about:blank")
        pos = page.evaluate(
            "(async()=>{const p=await new Promise((res,rej)=>"
            "navigator.geolocation.getCurrentPosition(res,rej));"
            "return [p.coords.latitude, p.coords.longitude,"
            "p.coords instanceof GeolocationCoordinates];})()",
            await_promise=True)
        assert abs(pos[0] - 35.6895) < 0.05
        assert abs(pos[1] - 139.6917) < 0.05
        assert pos[2] is True
    finally:
        b.stop()


def test_launch_explicit_geolocation_beats_geo(fake_geo_chain, vanilla_path):
    from veilbrowser import launch
    p = FingerprintProfile(seed=126, platform="windows", language="en-US",
                           timezone="America/New_York",
                           geolocation=(48.8566, 2.3522),
                           proxy=f"http://127.0.0.1:{fake_geo_chain.port}")
    b = launch(p, engine="js", binary=vanilla_path)
    try:
        assert b.profile.geolocation == (48.8566, 2.3522)
        assert b.profile.webrtc_ip == "203.0.113.7"
        page = b.new_page("about:blank")
        lat = page.evaluate(
            "(async()=>{const p=await new Promise((res,rej)=>"
            "navigator.geolocation.getCurrentPosition(res,rej));"
            "return p.coords.latitude;})()", await_promise=True)
        assert abs(lat - 48.8566) < 0.05
    finally:
        b.stop()
