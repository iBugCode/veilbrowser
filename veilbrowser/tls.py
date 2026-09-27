"""Local TLS ClientHello capture + ja3 fingerprinting.

The JS/kernel patch layer never touches the network stack, but this makes
the claim verifiable: a local server records the raw ClientHello, we parse
it into the ja3 tuple (version, ciphers, extensions, curves, point formats)
so tests can assert that a rebuilt kernel produces the byte-identical
Chrome 153 handshake of the stock binary.
"""

from __future__ import annotations

import hashlib
import socket
import ssl
import threading

__all__ = ["TLSProbeServer", "ClientHello"]


class ClientHello:
    def __init__(self, version, ciphers, extensions, curves, point_formats):
        self.version = version
        self.ciphers = ciphers
        self.extensions = extensions
        self.curves = curves
        self.point_formats = point_formats

    @property
    def ja3_string(self) -> str:
        return ",".join([
            self.version,
            "-".join(self.ciphers),
            "-".join(self.extensions),
            "-".join(self.curves),
            "-".join(self.point_formats),
        ])

    @staticmethod
    def _norm(values) -> list[str]:
        # BoringSSL randomizes GREASE (0x?a?a) per connection, so byte-equal
        # ja3 comparison must normalize those slots away first.
        return ["G" if (int(v) & 0x0F0F) == 0x0A0A else v for v in values]

    @property
    def ja3_normalized(self) -> str:
        # Extensions are compared order-insensitively: BoringSSL permutes
        # extension order per connection, so only the SET is identity.
        # supported_groups carries per-connection GREASE curves too
        # (X25519GREASE etc.), point_formats is constant {0}.
        return ",".join([
            self.version,
            "-".join(self._norm(self.ciphers)),
            "-".join(sorted(self._norm(self.extensions))),
            "-".join(self._norm(self.curves)),
            "-".join(self._norm(self.point_formats)),
        ])

    @property
    def ja3_hash(self) -> str:
        return hashlib.md5(self.ja3_string.encode()).hexdigest()

    @property
    def has_grease(self) -> bool:
        """GREASE values (0x?0?0 pattern) must be present like real Chrome."""
        return any((int(c) & 0x0F0F) == 0x0A0A for c in self.ciphers)


class TLSProbeServer:
    """One-shot TLS capture server: accepts connections, records the raw
    ClientHello of each, and answers nothing (the client aborts on its own
    after the handshake stalls — the hello is already sent)."""

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self.hellos: list[ClientHello] = []
        self._lock = threading.Lock()
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(4)
        self._srv.settimeout(0.5)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        deadline = _mono() + self.timeout
        while _mono() < deadline and not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(3)
            try:
                raw = conn.recv(16384)
                hello = parse_client_hello(raw)
                if hello:
                    with self._lock:
                        self.hellos.append(hello)
            except OSError:
                pass
            finally:
                conn.close()

    def wait_for(self, n: int = 1, timeout: float = 15.0) -> list[ClientHello]:
        deadline = _mono() + timeout
        while _mono() < deadline:
            with self._lock:
                if len(self.hellos) >= n:
                    return list(self.hellos)
            _sleep(0.05)
        with self._lock:
            return list(self.hellos)

    def stop(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


def parse_client_hello(record: bytes) -> ClientHello | None:
    """Parse a TLS 1.2/1.3 record containing a ClientHello into ja3 fields."""
    if len(record) < 43 or record[0] != 0x16:  # handshake record
        return None
    pos = 5  # TLS record header
    if record[pos] != 0x01:  # ClientHello
        return None
    # handshake length (3 bytes)
    pos += 4
    pos += 2  # client_version
    pos += 32  # random
    slen = record[pos]  # session id length is a single byte
    pos += 1 + slen  # session id
    clen = int.from_bytes(record[pos:pos + 2], "big")
    pos += 2
    ciphers = [int.from_bytes(record[pos + i:pos + i + 2], "big")
               for i in range(0, clen, 2)]
    pos += clen
    plen = int.from_bytes(record[pos:pos + 1], "big")
    pos += 1 + plen  # compression methods
    if pos >= len(record):
        return None
    exts_len = int.from_bytes(record[pos:pos + 2], "big")
    pos += 2
    extensions, curves, point_formats = [], [], []
    end = pos + exts_len
    while pos + 4 <= end and pos + 4 <= len(record):
        etype = int.from_bytes(record[pos:pos + 2], "big")
        elen = int.from_bytes(record[pos + 2:pos + 4], "big")
        body = record[pos + 4:pos + 4 + elen]
        extensions.append(etype)
        if etype == 0x000A:  # supported_groups
            n = int.from_bytes(body[0:2], "big")
            curves = [int.from_bytes(body[2 + i:4 + i], "big")
                      for i in range(0, n, 2)]
        elif etype == 0x000B:  # ec_point_formats
            point_formats = list(body[1:])
        pos += 4 + elen
    return ClientHello(
        version=str(int.from_bytes(record[9:11], "big")),
        ciphers=[str(c) for c in ciphers],
        extensions=[str(e) for e in extensions],
        curves=[str(c) for c in curves],
        point_formats=[str(p) for p in point_formats],
    )


from time import monotonic as _mono, sleep as _sleep  # noqa: E402
