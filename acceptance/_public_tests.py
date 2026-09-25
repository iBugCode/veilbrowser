"""Acceptance: run veilbrowser against public fingerprint test pages."""
from __future__ import annotations

import json
import sys
import time

def wait_for(ms):
    time.sleep(ms / 1000)

from veilbrowser import from_preset, launch


def sannysoft(page) -> dict:
    page.navigate("https://bot.sannysoft.com/")
    wait_for(4000)
    return page.evaluate("""
      [...document.querySelectorAll('tr')].slice(1).map(tr => {
        const tds = [...tr.querySelectorAll('td')];
        return [tds[0]?.innerText.trim(), tds[1]?.className, tds[1]?.innerText.trim()];
      })
    """)


def creepjs(page) -> dict:
    page.navigate("https://abrahamjuliot.github.io/creepjs/tests/worker.html")
    wait_for(2000)
    page.navigate("https://abrahamjuliot.github.io/creepjs/")
    for _ in range(20):
        wait_for(1000)
        trust = page.evaluate(
            "document.querySelector('.trust-score')?.innerText")
        if trust:
            break
    lies = page.evaluate(
        "[...document.querySelectorAll('.lies-list li, .warning')].map(e => e.innerText.trim()).slice(0, 40)")
    fp = page.evaluate(
        "document.querySelector('.fingerprint-id, #fingerprint-id')?.innerText")
    return {"trust": trust, "fingerprint": fp, "lies": [l for l in (lies or []) if l]}


def browserleaks(page, path: str) -> dict:
    page.navigate(f"https://browserleaks.com/{path}")
    wait_for(5000)
    return {"title": page.evaluate("document.title")}


def main() -> int:
    prof = from_preset("windows-us-office", seed=1001)
    b = launch(prof, headless=True, engine="js")
    out = {}
    try:
        with b.new_page() as page:
            out["sannysoft"] = sannysoft(page)
            out["creepjs"] = creepjs(page)
    finally:
        b.stop()
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
