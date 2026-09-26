"""Behavior-layer humanization: mouse, keyboard, scroll.

Emulates CloakBrowser/Camoufox-Cursory style input: bezier mouse paths with
per-step delays and overshoot, humanized keyboard cadence, eased wheel
scrolling.  Everything goes through the CDP Input domain, so every event the
page sees is ``isTrusted === true`` — indistinguishable from a real user at
the event level (bots that inject synthetic DOM events fail this check).

Deterministic: pass a seed (e.g. the profile seed) to get reproducible
behavior for tests; omit it for fresh randomness per instance.
"""

from __future__ import annotations

import math
import random
import time

__all__ = ["HumanInput", "bezier_path"]


def bezier_path(p0, p3, rng, n=30, bend=None, overshoot=None):
    """Cubic bezier from p0 to p3 with human-ish control points.

    Returns a list of (x, y) floats of length n+1 (inclusive endpoints).
    The two control points are offset perpendicular to the chord so the
    path arcs like a hand-drawn stroke; ``overshoot`` extends the endpoint
    along the chord direction and the path curls back (caller settles).
    """
    (x0, y0), (x3, y3) = p0, p3
    dx, dy = x3 - x0, y3 - y0
    dist = math.hypot(dx, dy) or 1.0
    # unit normal to the chord
    nx, ny = -dy / dist, dx / dist
    bend = bend if bend is not None else rng.uniform(0.05, 0.25) * (1, -1)[rng.randrange(2)]
    c1 = (x0 + dx * 0.3 + nx * dist * bend, y0 + dy * 0.3 + ny * dist * bend)
    c2 = (x0 + dx * 0.7 + nx * dist * bend * rng.uniform(0.3, 0.8),
          y0 + dy * 0.7 + ny * dist * bend * rng.uniform(0.3, 0.8))
    if overshoot:
        x3 += dx / dist * overshoot
        y3 += dy / dist * overshoot
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        x = mt**3 * x0 + 3 * mt**2 * t * c1[0] + 3 * mt * t**2 * c2[0] + t**3 * x3
        y = mt**3 * y0 + 3 * mt**2 * t * c1[1] + 3 * mt * t**2 * c2[1] + t**3 * y3
        pts.append((x, y))
    return pts


class HumanInput:
    """Human-like input driver bound to a :class:`veilbrowser.cdp.CDP` page."""

    def __init__(self, cdp, seed: int | None = None,
                 step_delay=(4, 14), key_delay=(55, 175), hold_delay=(45, 120)):
        self.cdp = cdp
        self.rng = random.Random(seed)
        self.step_delay = step_delay
        self.key_delay = key_delay
        self.hold_delay = hold_delay
        self.x, self.y = 0.0, 0.0
        self._buttons = 0
        self._moved = False

    # -- internals ------------------------------------------------------------

    def _sleep(self, lo, hi=None):
        hi = lo if hi is None else hi
        time.sleep(self.rng.uniform(lo, hi) / 1000.0)

    def _mouse(self, type_, x, y, **extra):
        params = {"type": type_, "x": round(x), "y": round(y), "buttons": self._buttons}
        if type_ == "mouseMoved" and not self._moved:
            # first move: a real pointer starts somewhere; flag it so the
            # renderer coalesces like a fresh session
            params["buttons"] = 0
        self.cdp.call("Input.dispatchMouseEvent", **{**params, **extra})

    def _key(self, type_, key, code, vkc, text=None, modifiers=0):
        params = {"type": type_, "key": key, "code": code,
                  "windowsVirtualKeyCode": vkc, "nativeVirtualKeyCode": vkc,
                  "modifiers": modifiers}
        if text is not None:
            params["text"] = text
        self.cdp.call("Input.dispatchKeyEvent", **params)

    # -- mouse ------------------------------------------------------------------

    def move(self, x: float, y: float, overshoot: bool | None = None) -> None:
        """Move the pointer along a bezier path to (x, y)."""
        x, y = float(x), float(y)
        if not self._moved:
            # first move of the session: approach from a plausible start
            self.x, self.y = x + self.rng.uniform(-200, 200), y + self.rng.uniform(-150, 150)
            self._moved = True
        dist = math.hypot(x - self.x, y - self.y)
        if dist < 2:
            self._mouse("mouseMoved", x, y)
            self.x, self.y = x, y
            return
        os_ = None
        if overshoot is None:
            overshoot = dist > 120
        if overshoot:
            os_ = dist * self.rng.uniform(0.04, 0.12)
        n = max(8, min(60, int(dist / self.rng.uniform(9, 18))))
        pts = bezier_path((self.x, self.y), (x, y), self.rng, n=n, overshoot=os_)
        for px, py in pts:
            self._mouse("mouseMoved", px, py)
            self._sleep(*self.step_delay)
        if os_:
            # settle back from the overshoot tip in a few micro steps
            tip = pts[-1]
            for i in range(1, 5):
                t = i / 4
                self._mouse("mouseMoved", tip[0] + (x - tip[0]) * t,
                            tip[1] + (y - tip[1]) * t)
                self._sleep(*self.step_delay)
        self.x, self.y = x, y

    def click(self, x: float | None = None, y: float | None = None,
              button: str = "left", clicks: int = 1) -> None:
        """Move to (x, y) (if given) and click with human hold timing."""
        if x is not None and y is not None:
            self.move(x, y)
            self._sleep(30, 90)  # aim-pause between arriving and pressing
        code = {"left": 0, "middle": 1, "right": 2}[button]
        for i in range(clicks):
            self._buttons = 1 << code
            self._mouse("mousePressed", self.x, self.y, button=button,
                        buttons=self._buttons, clickCount=i + 1)
            self._sleep(*self.hold_delay)
            self._mouse("mouseReleased", self.x, self.y, button=button,
                        buttons=0, clickCount=i + 1)
            self._buttons = 0
            if i < clicks - 1:
                self._sleep(60, 140)

    # -- keyboard ---------------------------------------------------------------

    _SPECIAL = {
        "\n": ("Enter", "Enter", 13), "\t": ("Tab", "Tab", 9),
        "\b": ("Backspace", "Backspace", 8),
    }

    def type_text(self, text: str, delay_scale: float = 1.0) -> None:
        """Type with per-key cadence: base 55-175ms, longer after sentence
        ends, occasional 2-3x thinking pause."""
        for i, ch in enumerate(text):
            if ch in self._SPECIAL:
                key, code, vkc = self._SPECIAL[ch]
                self._key("rawKeyDown", key, code, vkc)
                self._key("keyUp", key, code, vkc)
            elif ch == " ":
                self._key("keyDown", " ", "Space", 32, text=" ")
                self._key("keyUp", " ", "Space", 32)
            else:
                upper = ch.isupper()
                base = ch.upper() if upper else ch
                if upper:
                    self._key("keyDown", "Shift", "ShiftLeft", 16)
                vkc = ord(base.upper())
                self._key("keyDown", base, f"Key{base.upper()}" if base.isalpha()
                          else code_for(base), vkc, text=ch)
                self._key("keyUp", base, f"Key{base.upper()}" if base.isalpha()
                          else code_for(base), vkc)
                if upper:
                    self._key("keyUp", "Shift", "ShiftLeft", 16)
            self._sleep(self.key_delay[0] * delay_scale, self.key_delay[1] * delay_scale)
            if ch in ".!?\n":
                self._sleep(120, 380)  # sentence boundary
            elif self.rng.random() < 0.06:
                self._sleep(200, 550)  # thinking pause

    # -- scroll -----------------------------------------------------------------

    def scroll(self, dy: float, x: float | None = None, y: float | None = None) -> None:
        """Smooth wheel scroll of dy pixels (positive = down) in eased chunks."""
        if x is not None and y is not None:
            self.move(x, y)
        remaining, direction = abs(dy), 1 if dy >= 0 else -1
        speed = 0.0
        while remaining > 0:
            speed = min(speed + self.rng.uniform(0.15, 0.35), 1.0)
            if remaining < 200:  # decelerate at the tail
                speed = max(0.25, speed * 0.85)
            chunk = min(remaining, self.rng.uniform(60, 160) * speed)
            self._mouse("mouseWheel", self.x, self.y,
                        deltaX=0, deltaY=round(direction * chunk),
                        button="none", buttons=0)
            remaining -= chunk
            self._sleep(8, 25)


def code_for(ch: str) -> str:
    """Physical key code for non-alpha printable ASCII."""
    if ch.isdigit():
        return f"Digit{ch}"
    return {
        "-": "Minus", "=": "Equal", "[": "BracketLeft", "]": "BracketRight",
        "\\": "Backslash", ";": "Semicolon", "'": "Quote", ",": "Comma",
        ".": "Period", "/": "Slash", "`": "Backquote",
    }.get(ch, "Unidentified")
