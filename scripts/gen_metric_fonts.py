#!/usr/bin/env python3
"""Generate third_party/blink/renderer/platform/fonts/veil_metric_fonts.cc.

Sources (scripts/kernel-fonts/*.ttf): metric-compatible Latin fonts backing
the spoofed Windows/macOS platform font lists.

  arial            Liberation Sans   (Arial metrics)
  times_new_roman  Liberation Serif  (Times New Roman metrics)
  courier_new      Liberation Mono   (Courier New metrics)
  calibri          Carlito           (Calibri metrics)
  cambria          Caladea           (Cambria metrics)
  georgia          Gelasio           (Georgia metrics)

Refresh the TTFs with: python3 scripts/kernel-fonts/refresh.py (apt
fonts-liberation2 fonts-crosextra-carlito fonts-crosextra-caladea +
google/fonts Gelasio), then run this script and rebuild the kernel.

Usage: python3 scripts/gen_metric_fonts.py [--tree <chromium checkout>]
"""
from __future__ import annotations

import argparse
import base64
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "kernel-fonts")

# file stem -> embedded key (the name VeilEmbeddedFontKey() resolves to)
KEYS = {
    "arial": "arial",
    "calibri": "calibri",
    "cambria": "cambria",
    "courier_new": "courier new",
    "georgia": "georgia",
    "times_new_roman": "times new roman",
}

HEADER = "// Copyright 2026 The veilbrowser Authors. BSD-style license.\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default="/home/Project/build/src")
    args = ap.parse_args()

    fonts: dict[str, str] = {}
    for stem, key in KEYS.items():
        path = os.path.join(FONT_DIR, stem + ".ttf")
        fonts[key] = base64.b64encode(open(path, "rb").read()).decode()

    chunks = []
    for key, b64 in fonts.items():
        lits = [f'    "{b64[i:i+96]}"' for i in range(0, len(b64), 96)]
        chunks.append("    {\"%s\",\n%s},\n" % (key, "\n".join(lits)))

    out = os.path.join(args.tree, "third_party/blink/renderer/platform/fonts/veil_metric_fonts.cc")
    text = open(out).read()
    start = text.index("constexpr EmbeddedFont kEmbeddedFonts[] = {\n") + len(
        "constexpr EmbeddedFont kEmbeddedFonts[] = {\n")
    end = text.index("};", start)
    text = text[:start] + "".join(chunks) + text[end:]
    open(out, "w").write(text)
    total = sum(len(v) for v in fonts.values())
    print(f"veil_metric_fonts.cc regenerated ({total:,} b64 chars, {len(fonts)} fonts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
