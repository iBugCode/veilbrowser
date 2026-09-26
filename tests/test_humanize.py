"""Behavior-layer humanize: bezier mouse, keyboard cadence, smooth scroll.

Unit tests drive a fake CDP that records every dispatch; the E2E tests run
against the vanilla kernel and assert ``isTrusted === true`` on real events.
"""

from __future__ import annotations

import math

import pytest

from veilbrowser.humanize import HumanInput, bezier_path, code_for
from veilbrowser.profile import FingerprintProfile


def _resolved(seed: int, **kw):
    return FingerprintProfile(seed=seed, **kw).resolved()


class FakeCDP:
    def __init__(self):
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        return {}


@pytest.fixture()
def fake():
    return FakeCDP()


# ------------------------------------------------------------------ unit ---


class TestBezierPath:
    def test_endpoints_inclusive(self):
        pts = bezier_path((0, 0), (400, 200), __import__("random").Random(1), n=20)
        assert pts[0] == (0, 0)
        assert math.hypot(pts[-1][0] - 400, pts[-1][1] - 200) <= 60  # may overshoot

    def test_deterministic_with_seed(self):
        import random
        a = bezier_path((0, 0), (300, 100), random.Random(7), n=15, overshoot=0)
        b = bezier_path((0, 0), (300, 100), random.Random(7), n=15, overshoot=0)
        assert a == b

    def test_path_is_not_a_straight_line(self):
        import random
        pts = bezier_path((0, 0), (500, 0), random.Random(3), n=30, overshoot=0)
        max_dev = max(abs(y) for _, y in pts[1:-1])
        assert max_dev > 5  # hand-drawn arcs bend

    def test_length_scales_with_n(self):
        import random
        assert len(bezier_path((0, 0), (10, 10), random.Random(2), n=5)) == 6


class TestMouseUnit:
    def test_move_emits_path_toward_target(self, fake):
        h = HumanInput(fake, seed=42)
        h.move(300, 200)
        moves = [p for m, p in fake.calls if m == "Input.dispatchMouseEvent"
                 and p["type"] == "mouseMoved"]
        assert len(moves) >= 8
        assert (moves[-1]["x"], moves[-1]["y"]) == (300, 200)
        assert h.x == 300 and h.y == 200

    def test_move_step_count_scales_with_distance(self, fake):
        h = HumanInput(fake, seed=1)
        h.move(40, 40)
        short = len([p for m, p in fake.calls if p.get("type") == "mouseMoved"])
        fake.calls.clear()
        h.move(900, 700)
        long_ = len([p for m, p in fake.calls if p.get("type") == "mouseMoved"])
        assert long_ > short * 2

    def test_click_sequence_pressed_released(self, fake):
        h = HumanInput(fake, seed=5)
        h.click(150, 120)
        types = [p["type"] for m, p in fake.calls
                 if m == "Input.dispatchMouseEvent"]
        assert "mousePressed" in types and "mouseReleased" in types
        press = next(p for m, p in fake.calls
                     if m == "Input.dispatchMouseEvent" and p.get("type") == "mousePressed")
        release = next(p for m, p in fake.calls
                       if m == "Input.dispatchMouseEvent" and p.get("type") == "mouseReleased")
        assert press["button"] == "left" and press["buttons"] == 1
        assert release["buttons"] == 0
        assert press["clickCount"] == 1

    def test_double_click_counts(self, fake):
        h = HumanInput(fake, seed=5)
        h.click(50, 50, clicks=2)
        counts = [p["clickCount"] for m, p in fake.calls
                  if m == "Input.dispatchMouseEvent" and p.get("type") == "mousePressed"]
        assert counts == [1, 2]

    def test_right_button(self, fake):
        h = HumanInput(fake, seed=5)
        h.click(50, 50, button="right")
        press = next(p for m, p in fake.calls
                     if m == "Input.dispatchMouseEvent" and p.get("type") == "mousePressed")
        assert press["button"] == "right" and press["buttons"] == 4


class TestKeyboardUnit:
    def test_letters_get_text_and_codes(self, fake):
        h = HumanInput(fake, seed=9)
        h.type_text("ab")
        keys = [p for m, p in fake.calls if m == "Input.dispatchKeyEvent"]
        assert keys[0]["type"] == "keyDown" and keys[0]["text"] == "a"
        assert keys[0]["key"] == "a" and keys[0]["code"] == "KeyA"
        assert keys[0]["windowsVirtualKeyCode"] == 65
        downs = [p["type"] for p in keys]
        assert downs.count("keyDown") == 2 and downs.count("keyUp") == 2

    def test_uppercase_wraps_in_shift(self, fake):
        h = HumanInput(fake, seed=9)
        h.type_text("A")
        keys = [p for m, p in fake.calls if m == "Input.dispatchKeyEvent"]
        assert keys[0]["code"] == "ShiftLeft"
        assert keys[1]["text"] == "A" and keys[1]["key"] == "A"
        assert keys[-1]["code"] == "ShiftLeft" and keys[-1]["type"] == "keyUp"

    def test_space_and_enter(self, fake):
        h = HumanInput(fake, seed=9)
        h.type_text(" \n")
        keys = [p for m, p in fake.calls if m == "Input.dispatchKeyEvent"]
        assert keys[0]["code"] == "Space" and keys[0]["text"] == " "
        assert keys[2]["key"] == "Enter" and keys[2]["windowsVirtualKeyCode"] == 13

    def test_digits(self, fake):
        h = HumanInput(fake, seed=9)
        h.type_text("5")
        keys = [p for m, p in fake.calls if m == "Input.dispatchKeyEvent"]
        assert keys[0]["code"] == "Digit5"
        assert keys[0]["windowsVirtualKeyCode"] == 53

    def test_cadence_is_seeded_deterministic(self, fake):
        import random
        assert HumanInput(fake, seed=9).rng.random() == \
            random.Random(9).random()


class TestScrollUnit:
    def test_wheel_chunks_sum_to_dy(self, fake):
        h = HumanInput(fake, seed=11)
        h.scroll(900)
        wheels = [p for m, p in fake.calls if m == "Input.dispatchMouseEvent"
                  and p.get("type") == "mouseWheel"]
        assert len(wheels) >= 4
        total = sum(p["deltaY"] for p in wheels)
        assert 750 < total <= 950  # chunk rounding, always downward
        assert all(p["deltaY"] > 0 for p in wheels)

    def test_scroll_up_negative(self, fake):
        h = HumanInput(fake, seed=11)
        h.scroll(-500)
        wheels = [p for m, p in fake.calls if m == "Input.dispatchMouseEvent"
                  and p.get("type") == "mouseWheel"]
        assert all(p["deltaY"] < 0 for p in wheels)


def test_code_for():
    assert code_for("5") == "Digit5"
    assert code_for("-") == "Minus"
    assert code_for(";") == "Semicolon"


# ------------------------------------------------------------------- e2e ---

RECORDER = """
window.__evts = [];
['mousemove','mousedown','mouseup','wheel'].forEach(t =>
  document.addEventListener(t, e => window.__evts.push(
    {type: t, trusted: e.isTrusted, x: e.clientX, y: e.clientY})));
document.addEventListener('keydown', e => window.__evts.push(
  {type: 'keydown', trusted: e.isTrusted, key: e.key}));
window.addEventListener('beforeunload', () => {});
"""


class TestHumanInputE2E:
    def test_events_are_trusted_and_land(self, js_probe_page):
        prof = _resolved(301)
        b, page = js_probe_page(prof)
        page.evaluate(RECORDER)
        h = HumanInput(page, seed=301)
        h.click(300, 200)
        h.type_text("Hi there.")
        h.scroll(400)
        evts = page.evaluate("window.__evts")
        assert evts, "no events captured"
        assert all(e["trusted"] for e in evts), \
            f"untrusted event in stream: {[e for e in evts if not e['trusted']]}"
        kinds = {e["type"] for e in evts}
        assert {"mousemove", "mousedown", "mouseup", "keydown", "wheel"} <= kinds
        last_move = [e for e in evts if e["type"] == "mousemove"][-1]
        assert (last_move["x"], last_move["y"]) == (300, 200)
        keys = [e["key"] for e in evts if e["type"] == "keydown"]
        assert "H" in keys and "i" in keys and " " in keys

    def test_wheel_actually_scrolls_the_page(self, js_probe_page):
        prof = _resolved(302)
        b, page = js_probe_page(prof)
        page.evaluate(RECORDER +
            "document.body.style.height='4000px';")
        h = HumanInput(page, seed=302)
        h.scroll(900)
        top = page.evaluate("window.scrollY")
        assert top > 200, f"wheel events did not scroll (scrollY={top})"
