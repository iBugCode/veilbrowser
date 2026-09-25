"""Test doubles: authenticated upstream HTTP/SOCKS5 proxies and a target site."""

from __future__ import annotations

import base64
import json
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed mid-handshake")
        buf += chunk
    return buf


def _recv_head(sock):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("closed mid-headers")
        buf += chunk
    return buf


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TargetSite:
    """Tiny origin server: GET / -> JSON echo. Records every request path."""

    def __init__(self):
        self.requests: list[str] = []
        self.user_agents: list[str] = []
        self.client_hints: dict[str, list[str]] = {}
        self.port = _free_port()
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(self.path)
                outer.user_agents.append(self.headers.get("User-Agent") or "")
                for h in ("sec-ch-ua", "sec-ch-ua-platform", "sec-ch-ua-mobile",
                          "sec-ch-ua-full-version-list"):
                    v = self.headers.get(h)
                    if v:
                        outer.client_hints.setdefault(h, []).append(v)
                body = json.dumps({"ok": True, "path": self.path}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/probe"

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


class UpstreamHTTPProxy:
    """HTTP proxy demanding Proxy-Authorization; supports CONNECT + absolute-GET."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.saw_auth: list[str] = []
        self.connects: list[str] = []
        self.port = _free_port()
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", self.port))
        self._srv.listen(32)
        self._stop = threading.Event()
        self._srv.settimeout(0.3)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _expected(self) -> str:
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return f"Basic {token}"

    def _serve(self):
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
            conn.settimeout(10)
            head = _recv_head(conn)
            line = head.split(b"\r\n", 1)[0].decode()
            method, uri, _ver = line.split(" ", 2)
            headers = {}
            for raw in head.split(b"\r\n")[1:]:
                if b":" in raw:
                    k, v = raw.split(b":", 1)
                    headers[k.strip().lower().decode()] = v.strip().decode()
            if headers.get("proxy-authorization") != self._expected():
                conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
                conn.close()
                return
            self.saw_auth.append(headers["proxy-authorization"])
            if method == "CONNECT":
                self.connects.append(uri)
                host, _, port_s = uri.rpartition(":")
                remote = socket.create_connection((host, int(port_s)), timeout=10)
                conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                self._relay(conn, remote)
                return
            # absolute-URI
            from urllib.parse import urlsplit
            parts = urlsplit(uri)
            remote = socket.create_connection((parts.hostname, parts.port or 80), timeout=10)
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            body = head.split(b"\r\n\r\n", 1)[1]
            req = f"{method} {path} HTTP/1.1\r\nHost: {parts.hostname}\r\nConnection: close\r\n\r\n".encode()
            remote.sendall(req + body)
            self._relay(conn, remote)
        except (OSError, ValueError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _relay(self, a, b):
        """Bidirectional pump until either side closes."""
        import threading as _t

        def pump(src, dst):
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

        ta = _t.Thread(target=pump, args=(a, b), daemon=True)
        tb = _t.Thread(target=pump, args=(b, a), daemon=True)
        ta.start()
        tb.start()
        ta.join()
        tb.join()
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass

    def stop(self):
        self._stop.set()
        self._srv.close()


class UpstreamSocks5Proxy:
    """SOCKS5 upstream demanding RFC 1929 username/password auth."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.saw_auth: list[tuple[str, str]] = []
        self.connects: list[str] = []
        self.port = _free_port()
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", self.port))
        self._srv.listen(32)
        self._stop = threading.Event()
        self._srv.settimeout(0.3)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
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
            conn.settimeout(10)
            ver, nmethods = _recv_exact(conn, 2)
            methods = _recv_exact(conn, nmethods)
            if 0x02 not in methods:
                conn.sendall(b"\x05\xff")
                return
            conn.sendall(b"\x05\x02")
            _recv_exact(conn, 1)  # RFC 1929 VER byte
            vlen = _recv_exact(conn, 1)[0]
            uname = _recv_exact(conn, vlen).decode()
            plen = _recv_exact(conn, 1)[0]
            passwd = _recv_exact(conn, plen).decode()
            if (uname, passwd) != (self.username, self.password):
                conn.sendall(b"\x01\x01")
                return
            self.saw_auth.append((uname, passwd))
            conn.sendall(b"\x01\x00")
            head = _recv_exact(conn, 4)
            atyp = head[3]
            if atyp == 0x01:
                host = socket.inet_ntoa(_recv_exact(conn, 4))
            elif atyp == 0x03:
                host = _recv_exact(conn, _recv_exact(conn, 1)[0]).decode()
            else:
                host = socket.inet_ntop(socket.AF_INET6, _recv_exact(conn, 16))
            port = struct.unpack(">H", _recv_exact(conn, 2))[0]
            if head[1] != 0x01:
                conn.sendall(b"\x05\x07\x00\x01" + b"\x00" * 6)
                return
            self.connects.append(f"{host}:{port}")
            remote = socket.create_connection((host, port), timeout=10)
            conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
            self._relay(conn, remote)
        except (OSError, ConnectionError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _relay(self, a, b):
        """Bidirectional pump until either side closes."""
        import threading as _t

        def pump(src, dst):
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

        ta = _t.Thread(target=pump, args=(a, b), daemon=True)
        tb = _t.Thread(target=pump, args=(b, a), daemon=True)
        ta.start()
        tb.start()
        ta.join()
        tb.join()
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass

    def stop(self):
        self._stop.set()
        self._srv.close()
