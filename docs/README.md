# veilbrowser documentation

| Document | Contents |
|---|---|
| [Fingerprint profiles](fingerprint.md) | Seeds, presets, JSON profile files, save/load workflow, every kernel switch, coherence rules |
| [Anti-detection coverage](anti-detection.md) | Every spoofed surface, DevTools/CDP invisibility, threat-model matrix, honest limitations |
| [Kernel build & CI](kernel.md) | Building veil-chromium from source, patch-series layout, GN flags, release pipeline |
| [Python API](python-api.md) | `launch`, `Browser`, `CDP`, profiles, presets, probing, humanization, upgrades |

Quick links: [README](../README.md) · [中文说明](../README.zh-CN.md) · [README (日本語)](../README.ja.md)

## The 60-second tour

```bash
pip install -e ".[cdp,dev]"
veilbrowser upgrade                          # fetch the prebuilt veil-chromium kernel
veilbrowser check --vanilla --seed 1001 --preset windows-us-office
```

```python
from veilbrowser import from_preset, launch

with launch(from_preset("windows-us-office", seed=42)) as browser:
    with browser.new_page("https://example.com") as page:
        print(page.evaluate("navigator.userAgent"))
    print(browser.kernel_active)   # True → C++ spoofing confirmed live
```

The kernel is a self-built Chromium (`veil-chromium`) whose fingerprint
surfaces live **in C++ inside Blink/V8** — nothing is injected into pages.
All identity state is carried by launch switches, so the same seed
reproduces the same fingerprint on every machine, every session.
