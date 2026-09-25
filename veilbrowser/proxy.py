"""Local proxy forwarder with upstream authentication.

fingerprint-chromium's ``--proxy-server`` does not support passwords
(same gap CloakBrowser fills). This module runs a tiny localhost proxy that
speaks both HTTP (absolute-URI + CONNECT) and SOCKS5 to the browser, and
forwards every connection to an upstream proxy, adding the credentials.

Design notes:
  * Auto-detects the client protocol from the first byte (0x05 = SOCKS5).
  * SOCKS5 UDP ASSOCIATE is rejected with RFC 6428 "command not supported"
    (0x07) — Chromium falls back to TCP; QUIC/WebRTC-over-UDP through the
    forwarder is a documented v1 gap (CloakBrowser added it in 146.4).
  * DNS is upstream-resolved: hostnames are forwarded as-is (SOCKS5 ATYP=domain,
    HTTP CONNECT host:port), so the browser never leaks DNS.
"""

from __future__ import annotations

import base64
import socket
import ssl
import struct
import threading
from dataclasses import dataclass

_SOCKS5 = 5
_CONNECT = 1
_UDP_ASSOCIATE = 3


class ProxyError(Exception):
    pass


@dataclass
class UpstreamProxy:
    scheme: str            # http | https | socks5
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def has_auth(self) -> bool:
        return self.username is not None


def parse_proxy_url(url: str) -> UpstreamProxy:
    """Parse scheme://[user[:pass]@]host:port into an UpstreamProxy."""
    from urllib.parse import urlparse

    u = urlparse(url)
    scheme = (u.scheme or "").lower()
    if scheme not in ("http", "https", "socks5", "socks5h", "socks"):
        raise ProxyError(f"unsupported proxy scheme in {url!r}")
    if scheme in ("socks", "socks5h"):
        scheme = "socks5"
    if not u.hostname or not u.port:
        raise ProxyError(f"proxy URL must be scheme://host:port, got {url!r}")
    return UpstreamProxy(scheme=scheme, host=u.hostname, port=u.port,
                         username=u.username, password=u.password)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ProxyError("connection closed mid-handshake")
        buf += chunk
    return buf


def _recv_until(sock: socket.socket, marker: bytes, seed: bytes = b"",
                limit: int = 65536) -> bytes:
    buf = seed
    while marker not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ProxyError("connection closed mid-headers")
        buf += chunk
        if len(buf) > limit:
            raise ProxyError("headers too large")
    return buf


def _basic_auth_header(up: UpstreamProxy) -> str:
    token = base64.b64encode(f"{up.username}:{up.password or ''}".encode()).decode()
    return f"Basic {token}"


# --------------------------- upstream dialers -------------------------------


def _socks5_upstream_connect(sock: socket.socket, up: UpstreamProxy,
                             host: str, port: int) -> None:
    method = 0x02 if up.has_auth else 0x00
    sock.sendall(bytes([_SOCKS5, 0x01, method]))
    resp = _recv_exact(sock, 2)
    if resp[0] != _SOCKS5:
        raise ProxyError(f"bad SOCKS5 upstream greeting: {resp.hex()}")
    if resp[1] == 0x02 and up.has_auth:
        uname = up.username.encode()
        passwd = (up.password or "").encode()
        sock.sendall(b"\x01" + bytes([len(uname)]) + uname +
                     bytes([len(passwd)]) + passwd)
        auth = _recv_exact(sock, 2)
        if auth[1] != 0x00:
            raise ProxyError("upstream SOCKS5 rejected credentials")
    elif resp[1] != 0x00:
        raise ProxyError(f"upstream SOCKS5 chose unsupported method {resp[1]:#x}")

    host_b = host.encode("idna") if any(ord(c) > 127 for c in host) else host.encode()
    if ":" in host:
        addr = b"\x04" + socket.inet_pton(socket.AF_INET6, host)
    elif all(c.isdigit() or c == "." for c in host) and host.count(".") == 3:
        addr = b"\x01" + socket.inet_aton(host)
    else:
        addr = b"\x03" + bytes([len(host_b)]) + host_b
    sock.sendall(bytes([_SOCKS5, _CONNECT, 0x00]) + addr + struct.pack(">H", port))
    reply = _recv_exact(sock, 4)
    if reply[1] != 0x00:
        raise ProxyError(f"upstream SOCKS5 CONNECT failed, reply {reply[1]:#x}")
    atyp = reply[3]
    if atyp == 0x01:
        _recv_exact(sock, 4 + 2)
    elif atyp == 0x03:
        _recv_exact(sock, _recv_exact(sock, 1)[0] + 2)
    elif atyp == 0x04:
        _recv_exact(sock, 16 + 2)
    else:
        raise ProxyError(f"unknown SOCKS5 bind ATYP {atyp:#x}")


def _http_upstream_connect(sock: socket.socket, up: UpstreamProxy,
                           host: str, port: int) -> None:
    req = (f"CONNECT {host}:{port} HTTP/1.1\r\n"
           f"Host: {host}:{port}\r\n")
    if up.has_auth:
        req += f"Proxy-Authorization: {_basic_auth_header(up)}\r\n"
    req += "\r\n"
    sock.sendall(req.encode())
    head = _recv_until(sock, b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0]
    parts = status.split(b" ", 2)
    if len(parts) < 2 or not parts[1].startswith(b"2"):
        raise ProxyError(f"upstream HTTP CONNECT failed: {status!r}")


def _dial_upstream(up: UpstreamProxy, host: str, port: int,
                   timeout: float = 15.0) -> socket.socket:
    sock = socket.create_connection((up.host, up.port), timeout=timeout)
    sock.settimeout(None)
    try:
        if up.scheme == "socks5":
            _socks5_upstream_connect(sock, up, host, port)
        else:
            if up.scheme == "https":
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                sock = ctx.wrap_socket(sock, server_hostname=up.host)
            _http_upstream_connect(sock, up, host, port)
    except Exception:
        sock.close()
        raise
    return sock


# ------------------------------- relaying -----------------------------------


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _relay(a: socket.socket, b: socket.socket, seed_b: bytes = b"") -> None:
    if seed_b:
        b.sendall(seed_b)
    ta = threading.Thread(target=_pump, args=(a, b), daemon=True)
    tb = threading.Thread(target=_pump, args=(b, a), daemon=True)
    ta.start(); tb.start()
    ta.join(); tb.join()
    a.close(); b.close()


# --------------------------- client-side handlers ---------------------------


def _serve_socks5(conn: socket.socket, up: UpstreamProxy, first: int) -> None:
    try:
        nmethods = _recv_exact(conn, 2)[1]
        _recv_exact(conn, nmethods)  # methods offered; we always pick "no auth"
        conn.sendall(bytes([_SOCKS5, 0x00]))
        head = _recv_exact(conn, 4)
        if head[0] != _SOCKS5:
            raise ProxyError("bad SOCKS5 request version")
        cmd, _, atyp = head[1], head[2], head[3]
        if cmd == _UDP_ASSOCIATE:
            _recv_exact(conn, 6 if atyp == 0x01 else (4 + 2 if atyp == 0x03 else 18))
            conn.sendall(bytes([_SOCKS5, 0x07, 0x00, 0x01]) + b"\x00\x00\x00\x00\x00\x00")
            return
        if cmd != _CONNECT:
            conn.sendall(bytes([_SOCKS5, 0x07, 0x00, 0x01]) + b"\x00\x00\x00\x00\x00\x00")
            return
        if atyp == 0x01:
            host = socket.inet_ntoa(_recv_exact(conn, 4))
        elif atyp == 0x03:
            host = _recv_exact(conn, _recv_exact(conn, 1)[0]).decode()
        elif atyp == 0x04:
            host = socket.inet_ntop(socket.AF_INET6, _recv_exact(conn, 16))
        else:
            conn.sendall(bytes([_SOCKS5, 0x08, 0x00, 0x01]) + b"\x00\x00\x00\x00\x00\x00")
            return
        port = struct.unpack(">H", _recv_exact(conn, 2))[0]
        try:
            remote = _dial_upstream(up, host, port)
        except (ProxyError, OSError):
            conn.sendall(bytes([_SOCKS5, 0x05, 0x00, 0x01]) + b"\x00\x00\x00\x00\x00\x00")
            return
        conn.sendall(bytes([_SOCKS5, 0x00, 0x00, 0x01]) + b"\x00\x00\x00\x00\x00\x00")
        _relay(conn, remote)
    except (ProxyError, OSError):
        try:
            conn.close()
        except OSError:
            pass


def _serve_http(conn: socket.socket, up: UpstreamProxy, first: int) -> None:
    try:
        head = _recv_until(conn, b"\r\n\r\n", seed=bytes([first]))
        raw_head, rest = head.split(b"\r\n\r\n", 1)
        lines = raw_head.split(b"\r\n")
        method, uri, ver = lines[0].decode("latin-1").split(" ", 2)

        if method == "CONNECT":
            host, _, port_s = uri.rpartition(":")
            port = int(port_s)
            try:
                remote = _dial_upstream(up, host, port)
            except (ProxyError, OSError):
                conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                conn.close()
                return
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            _relay(conn, remote, seed_b=rest)
            return

        # absolute-URI request (plain http:// page) -> origin-form upstream
        headers = []
        for line in lines[1:]:
            name = line.split(b":", 1)[0].strip().lower()
            if name in (b"proxy-authorization", b"proxy-connection", b"connection",
                        b"keep-alive", b"proxy-authenticate"):
                continue
            headers.append(line)
        if uri.startswith("http://"):
            from urllib.parse import urlsplit
            parts = urlsplit(uri)
            target_host = parts.hostname or ""
            target_port = parts.port or 80
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
        else:
            target_host, target_port = uri, 80
            path = "/"
        headers.append(f"Host: {target_host}".encode())
        headers.append(b"Connection: close")
        if up.has_auth:
            headers.append(f"Proxy-Authorization: {_basic_auth_header(up)}".encode())
        request_line = f"{method} {path} {ver}\r\n".encode()
        blob = request_line + b"\r\n".join(headers) + b"\r\n\r\n"
        try:
            remote = _dial_upstream(up, target_host, target_port)
        except (ProxyError, OSError):
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            conn.close()
            return
        _relay(conn, remote, seed_b=blob + rest)
    except (ProxyError, OSError, ValueError):
        try:
            conn.close()
        except OSError:
            pass


class LocalForwarder:
    """Listens on 127.0.0.1:<ephemeral> and forwards to an authenticated upstream."""

    def __init__(self, upstream: UpstreamProxy):
        self.upstream = upstream
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(64)
        self._srv.settimeout(0.5)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def local_port(self) -> int:
        return self._srv.getsockname()[1]

    def chrome_proxy_arg(self) -> str:
        scheme = "socks5" if self.upstream.scheme == "socks5" else "http"
        return f"{scheme}://127.0.0.1:{self.local_port}"

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(30)
        try:
            first = _recv_exact(conn, 1)[0]
            conn.settimeout(None)
        except (ProxyError, OSError):
            conn.close()
            return
        if first == _SOCKS5:
            _serve_socks5(conn, self.upstream, first)
        else:
            conn.settimeout(None)
            _serve_http(conn, self.upstream, first)

    def stop(self) -> None:
        self._stop.set()
        # shutdown() wakes a thread blocked in accept() so the listening
        # socket is released deterministically; close() alone can leave the
        # bind held until the accept timeout elapses.
        try:
            self._srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._srv.close()
        except OSError:
            pass
        self._thread.join(timeout=2)
