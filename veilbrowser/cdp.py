"""Minimal synchronous Chrome DevTools Protocol client.

Only what veilbrowser needs: open a page, evaluate JS (with awaitPromise),
navigate and wait for load. Depends on `websocket-client` (optional extra
`veilbrowser[cdp]`).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request

_BASE = "http://127.0.0.1"


class CDPError(Exception):
    pass


class CDP:
    def __init__(self, ws_url: str, timeout: float = 30.0):
        try:
            import websocket  # websocket-client
        except ImportError as e:  # pragma: no cover
            raise CDPError("pip install veilbrowser[cdp] for CDP support") from e
        self.ws_url = ws_url
        self.timeout = timeout
        self.ws = websocket.create_connection(ws_url, timeout=timeout,
                                              suppress_origin=True)
        self._lock = threading.Lock()
        self._next_id = 0

    # -- low level -----------------------------------------------------------

    def call(self, method: str, **params):
        with self._lock:
            self._next_id += 1
            mid = self._next_id
            self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CDPError(f"timeout waiting for {method} reply")
                self.ws.settimeout(remaining)
                msg = json.loads(self.ws.recv())
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise CDPError(f"{method} failed: {msg['error']}")
                    return msg.get("result", {})

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def __enter__(self) -> "CDP":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- page helpers ----------------------------------------------------------

    def evaluate(self, expression: str, await_promise: bool = False):
        result = None
        for attempt in range(3):
            try:
                result = self.call("Runtime.evaluate", expression=expression,
                                   returnByValue=True, awaitPromise=await_promise)
                break
            except CDPError as e:
                # the page switches execution contexts while finishing load;
                # re-running the expression on the fresh context is standard
                if "Execution context was destroyed" in str(e) and attempt < 2:
                    time.sleep(0.15)
                    continue
                raise
        if "exceptionDetails" in result:
            raise CDPError(f"page exception: {result['exceptionDetails']!r}")
        return result.get("result", {}).get("value")

    def navigate(self, url: str, wait: bool = True, timeout: float = 30.0) -> None:
        self.call("Page.enable")
        self.call("Page.navigate", url=url)
        if not wait:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.evaluate("document.readyState")
            if state == "complete":
                return
            time.sleep(0.05)
        raise CDPError(f"navigation to {url!r} timed out")


# ----------------------- browser-level HTTP endpoints ------------------------


class DevTools:
    """HTTP side of the DevTools endpoint (/json/*)."""

    def __init__(self, port: int):
        self.port = port

    def _request(self, path: str, method: str = "GET") -> dict:
        req = urllib.request.Request(f"{_BASE}:{self.port}{path}", method=method)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def version(self) -> dict:
        return self._request("/json/version")

    def list_targets(self) -> list:
        return self._request("/json/list")

    def new_page(self, url: str = "about:blank") -> dict:
        from urllib.parse import quote
        return self._request(f"/json/new?{quote(url, safe='')}", method="PUT")

    def close_page(self, target_id: str) -> dict:
        return self._request(f"/json/close/{target_id}")

    def attach(self, ws_url: str) -> CDP:
        return CDP(ws_url)

    def new_page_cdp(self, url: str = "about:blank") -> CDP:
        target = self.new_page(url)
        return self.attach(target["webSocketDebuggerUrl"])
