#!/usr/bin/env python3
"""Regenerate the pure-C++ kernel patches (v0.9.0) into kernel-patches/.

Diffs the live Chromium build tree against the pre-edit snapshots taken in
/tmp/pristine9 (state after the existing series applied), so the generated
patches stack on top of the existing ones. Also refreshes 015 (measureText
Shuffle factor fix) whose upstream pristine is reconstructed inline.

NOTE: the speech_synthesis.cc section of 024 is maintained by hand since
v0.9.1 (host-voice replacement, camoufox #717) — regenerating 024 from a
stale v0.9.0-era pristine snapshot would silently revert it. Re-diff that
file against upstream by hand. 028-034 are hand-written (no generator
entries).

Usage: python3 scripts/gen_kernel_patches.py [--tree <checkout>]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO, "kernel-patches", "extra", "fingerprint")

# patch file -> (pristine path or None for upstream-reconstructed, [tree paths])
PATCHES = {
    "022-media-consistency.patch": [
        ("media_values.cc",
         "third_party/blink/renderer/core/css/media_values.cc"),
    ],
    "023-embedded-fonts.patch": [
        ("font_cache.cc",
         "third_party/blink/renderer/platform/fonts/font_cache.cc"),
        ("BUILD.gn", "third_party/blink/renderer/platform/BUILD.gn"),
        (None, "third_party/blink/renderer/platform/fonts/veil_metric_fonts.h"),
        (None, "third_party/blink/renderer/platform/fonts/veil_metric_fonts.cc"),
    ],
    "024-desktop-env.patch": [
        ("speech_synthesis.cc",
         "third_party/blink/renderer/modules/speech/speech_synthesis.cc"),
        ("storage_manager.cc",
         "third_party/blink/renderer/modules/quota/storage_manager.cc"),
        ("media_devices.cc",
         "third_party/blink/renderer/modules/mediastream/media_devices.cc"),
        ("audio_context.cc",
         "third_party/blink/renderer/modules/webaudio/audio_context.cc"),
    ],
    "025-webgl-limits.patch": [
        ("webgl_rendering_context_base.cc",
         "third_party/blink/renderer/modules/webgl/"
         "webgl_rendering_context_base.cc"),
    ],
    "026-window-dpr.patch": [
        ("local_dom_window.cc",
         "third_party/blink/renderer/core/frame/local_dom_window.cc"),
    ],
    "027-extension-paths.patch": [
        ("switches_upstream.h", "extensions/common/switches.h"),
        ("switches_upstream.cc", "extensions/common/switches.cc"),
        ("extension_service_upstream.h",
         "chrome/browser/extensions/extension_service.h"),
        ("extension_service_upstream.cc",
         "chrome/browser/extensions/extension_service.cc"),
    ],
}


def render(rel_paths_a: dict, rel_paths_b: dict) -> str:
    """git diff --no-index between two staged trees, normalised to a/ b/."""
    with tempfile.TemporaryDirectory() as tmp:
        a = os.path.join(tmp, "a")
        b = os.path.join(tmp, "b")
        for rel, src in rel_paths_a.items():
            dst = os.path.join(a, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        for rel, src in rel_paths_b.items():
            dst = os.path.join(b, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        r = subprocess.run(["git", "diff", "--no-index", "--", "a", "b"],
                           capture_output=True, text=True, cwd=tmp)
        text = r.stdout
        if not text.strip():
            raise RuntimeError("empty diff")
        # a/a/<rel> -> a/<rel>, b/b/<rel> -> b/<rel>
        text = text.replace("a/a/", "a/").replace("b/b/", "b/")
        return text.rstrip("\n") + "\n"


def fix_015(tree: str) -> str:
    """Rebuild 015 from the reconstructed upstream measureText section."""
    target = os.path.join(
        tree, "third_party/blink/renderer/modules/canvas/canvas2d/"
        "base_rendering_context_2d.cc")
    upstream = os.path.join(tempfile.gettempdir(),
                            "pristine9", "base_rendering_context_2d_upstream.cc")
    return render(
        {"third_party/blink/renderer/modules/canvas/canvas2d/"
         "base_rendering_context_2d.cc": upstream},
        {"third_party/blink/renderer/modules/canvas/canvas2d/"
         "base_rendering_context_2d.cc": target})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default="/home/Project/build/src")
    args = ap.parse_args()

    pristine = "/tmp/pristine9"
    written = []
    for patch_name, entries in PATCHES.items():
        rel_a: dict = {}
        rel_b: dict = {}
        for pris, tree_path in entries:
            rel = tree_path
            rel_b[rel] = os.path.join(args.tree, tree_path)
            if pris is None:  # new file
                rel_a[rel] = os.devnull
                # git diff needs a real empty file for the a-side
                rel_a[rel] = _empty_file()
            else:
                src = os.path.join(pristine, pris)
                if not os.path.isfile(src):
                    raise FileNotFoundError(f"pristine missing: {src}")
                rel_a[rel] = src
        text = render(rel_a, rel_b)
        out = os.path.join(OUT_DIR, patch_name)
        with open(out, "w") as f:
            f.write(text)
        written.append((patch_name, len(text)))

    text_015 = fix_015(args.tree)
    out015 = os.path.join(OUT_DIR, "015-canvas-measure-text.patch")
    with open(out015, "w") as f:
        f.write(text_015)
    written.append(("015-canvas-measure-text.patch", len(text_015)))

    for name, size in written:
        print(f"{name}: {size:,} bytes")
    print("done — update series ordering if needed")
    return 0


_empty_holder = {}


def _empty_file() -> str:
    import tempfile as _t
    fd, path = _t.mkstemp()
    os.close(fd)
    return path


if __name__ == "__main__":
    raise SystemExit(main())
