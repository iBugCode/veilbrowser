"""Fingerprint profile generation for veilbrowser.

Turns a bare integer seed into a *coherent* fingerprint profile: language,
timezone, CPU count and OS identity that agree with each other, plus the
chrome flags to realize it on fingerprint-chromium.

Coherence rules (the part fingerprint-chromium leaves to the caller):
  * navigator.languages follows --accept-lang, not --lang, so both are set.
  * timezone pool is derived from the locale so tz never contradicts language.
  * hardwareConcurrency is drawn from a realistic pool per platform.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field, fields, replace

PLATFORMS = ("windows", "macos", "linux")

# locale -> timezones that are geographically consistent with it
_LOCALE_TIMEZONES: dict[str, tuple[str, ...]] = {
    "en-US": ("America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"),
    "en-GB": ("Europe/London",),
    "de-DE": ("Europe/Berlin", "Europe/Vienna", "Europe/Zurich"),
    "fr-FR": ("Europe/Paris", "Europe/Brussels"),
    "es-ES": ("Europe/Madrid",),
    "it-IT": ("Europe/Rome",),
    "ru-RU": ("Europe/Moscow", "Europe/Samara"),
    "zh-CN": ("Asia/Shanghai",),
    "zh-TW": ("Asia/Taipei",),
    "ja-JP": ("Asia/Tokyo",),
    "ko-KR": ("Asia/Seoul",),
    "pt-BR": ("America/Sao_Paulo",),
    "en-IN": ("Asia/Kolkata",),
    "en-AU": ("Australia/Sydney", "Australia/Melbourne"),
    "en-CA": ("America/Toronto", "America/Vancouver"),
}

_LOCALES_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "windows": ("en-US", "en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "pt-BR", "zh-CN", "ja-JP", "ko-KR", "ru-RU", "en-IN"),
    "macos": ("en-US", "en-US", "en-GB", "de-DE", "fr-FR", "ja-JP", "ko-KR", "zh-CN"),
    "linux": ("en-US", "en-GB", "de-DE", "fr-FR", "ru-RU", "zh-CN", "en-IN"),
}

_CPU_POOLS: dict[str, tuple[int, ...]] = {
    "windows": (4, 6, 8, 8, 12, 16, 16, 20, 24),
    "macos": (8, 8, 10, 10, 12, 14, 16),
    "linux": (2, 4, 8, 16, 24, 32),
}

_BRANDS = ("Chrome", "Edge", "Opera", "Vivaldi")


def _rng(seed: int, *salt: str) -> random.Random:
    h = hashlib.sha256(f"{seed}:{':'.join(salt)}".encode()).digest()
    return random.Random(h)


@dataclass
class FingerprintProfile:
    """A coherent browser fingerprint configuration."""

    seed: int
    platform: str | None = None          # windows | macos | linux (None = host OS)
    platform_version: str | None = None  # e.g. "15.2.0" for macos, "10.0.0" for windows
    brand: str | None = None             # Chrome | Edge | Opera | Vivaldi (None = Chromium)
    brand_version: str | None = None     # None = kernel default for the brand
    timezone: str | None = None
    language: str | None = None          # e.g. "en-US"; drives both --lang and --accept-lang
    hardware_concurrency: int | None = None
    disable_spoofing: tuple[str, ...] = ()  # subset of font/audio/canvas/clientrects/gpu
    proxy: str | None = None             # scheme://[user:pass@]host:port
    geolocation: tuple[float, float] | None = None  # (lat, lon) for navigator.geolocation
    webrtc_ip: str | None = None         # public IP reported in WebRTC ICE candidates
    extra_flags: list[str] = field(default_factory=list)

    def resolved(self) -> "FingerprintProfile":
        """Fill unset fields deterministically from the seed, keeping coherence."""
        p = self
        rng = _rng(p.seed, "platform")
        if p.platform is None:
            p = replace(p, platform=rng.choice(("windows", "windows", "macos", "linux")))
        rng = _rng(p.seed, "locale", p.platform)
        if p.language is None:
            p = replace(p, language=rng.choice(_LOCALES_BY_PLATFORM[p.platform]))
        rng = _rng(p.seed, "tz", p.language)
        if p.timezone is None:
            p = replace(p, timezone=rng.choice(_LOCALE_TIMEZONES.get(p.language, ("UTC",))))
        rng = _rng(p.seed, "cpu", p.platform)
        if p.hardware_concurrency is None:
            p = replace(p, hardware_concurrency=rng.choice(_CPU_POOLS[p.platform]))
        rng = _rng(p.seed, "brand")
        if p.brand is None:
            p = replace(p, brand=rng.choice(("Chrome", "Chrome", "Chrome", "Edge", "Opera", "Vivaldi")))
        return p

    def accept_language(self) -> str:
        lang = self.language or "en-US"
        base = lang.split("-")[0]
        return lang if base == lang else f"{lang},{base}"

    # ---- persistence (CloakBrowser-style saved profiles) --------------------
    # Same saved profile + same seed reproduce the same fingerprint on every
    # engine, so sessions can be resumed weeks later with an identical identity.

    def to_dict(self) -> dict:
        """JSON-safe dict of every explicit field. Seed-derived fields are
        re-derived on load, so a saved file never goes stale."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FingerprintProfile":
        known = {f.name: f for f in fields(cls)}
        kwargs = {}
        for k, v in d.items():
            if k not in known:
                continue
            f = known[k]
            if isinstance(v, list) and isinstance(f.default, tuple):
                v = tuple(v)  # JSON has no tuples (disable_spoofing)
            kwargs[k] = v
        return cls(**kwargs)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, sort_keys=True)

    @classmethod
    def load(cls, path: str) -> "FingerprintProfile":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def fingerprint_flags(self, kernel_fp: bool = True) -> list[str]:
        """Chrome flags realizing this profile (proxy excluded — launcher handles it).

        kernel_fp=False emits only switches a vanilla kernel understands
        (--lang/--accept-lang): used with the JS injection engine.
        """
        flags: list[str] = []
        if kernel_fp:
            flags.append(f"--fingerprint={self.seed}")
            if self.platform:
                flags.append(f"--fingerprint-platform={self.platform}")
            if self.platform_version:
                flags.append(f"--fingerprint-platform-version={self.platform_version}")
            if self.brand:
                flags.append(f"--fingerprint-brand={self.brand}")
            if self.brand_version:
                flags.append(f"--fingerprint-brand-version={self.brand_version}")
            if self.hardware_concurrency:
                flags.append(f"--fingerprint-hardware-concurrency={self.hardware_concurrency}")
            if self.timezone:
                flags.append(f"--timezone={self.timezone}")
            from .inject import screen_metrics
            sm = screen_metrics(self)
            flags.append(f"--fingerprint-screen-width={sm['w']}")
            flags.append(f"--fingerprint-screen-height={sm['h']}")
            flags.append(f"--fingerprint-screen-avail-width={sm['availW']}")
            flags.append(f"--fingerprint-screen-avail-height={sm['availH']}")
        if self.language:
            flags.append(f"--lang={self.language}")
            flags.append(f"--accept-lang={self.accept_language()}")
        if kernel_fp and self.disable_spoofing:
            flags.append("--disable-spoofing=" + ",".join(self.disable_spoofing))
        flags.extend(self.extra_flags)
        return flags


# --- Presets: realistic "personas" in the spirit of CloakBrowser profiles ----

PRESETS: dict[str, dict] = {
    "windows-us-office": dict(platform="windows", language="en-US", brand="Chrome",
                              hardware_concurrency=8),
    "windows-us-gamer": dict(platform="windows", language="en-US", brand="Chrome",
                             hardware_concurrency=16),
    "windows-cn-office": dict(platform="windows", language="zh-CN", brand="Chrome",
                              hardware_concurrency=16),
    "macos-us-designer": dict(platform="macos", language="en-US", brand="Chrome",
                              hardware_concurrency=10),
    "macos-jp-creator": dict(platform="macos", language="ja-JP", brand="Vivaldi",
                             hardware_concurrency=10),
    "linux-dev-de": dict(platform="linux", language="de-DE", brand="Chromium",
                         hardware_concurrency=16),
}


def from_preset(name: str, seed: int) -> FingerprintProfile:
    if name not in PRESETS:
        raise KeyError(f"unknown preset {name!r}; available: {', '.join(PRESETS)}")
    return FingerprintProfile(seed=seed, **PRESETS[name]).resolved()
