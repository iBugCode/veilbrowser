"""Proxy-exit GeoIP alignment (Camoufox-style).

A Tokyo timezone behind a Frankfurt exit IP is one of the loudest
contradictions a fraud score can see.  When the profile leaves timezone
or language unset and a proxy is configured, veilbrowser asks a geo
service *through the same exit* and fills the gaps from the answer.
Fail-open: any network hiccup leaves the profile untouched rather than
delaying or blocking the launch.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import replace

from .profile import FingerprintProfile

_GEO_URL = ("http://ip-api.com/json/?fields=status,country,countryCode,"
            "timezone,lat,lon,query")

# Country -> locale consistent with profile.locale pools.  Unlisted
# countries fall back to en-US rather than an exotic locale a seed pool
# has no timezone entries for.
_COUNTRY_LOCALES: dict[str, str] = {
    "US": "en-US", "GB": "en-GB", "DE": "de-DE", "AT": "de-DE",
    "CH": "de-DE", "FR": "fr-FR", "BE": "fr-FR", "ES": "es-ES",
    "IT": "it-IT", "RU": "ru-RU", "CN": "zh-CN", "TW": "zh-TW",
    "JP": "ja-JP", "KR": "ko-KR", "BR": "pt-BR", "IN": "en-IN",
    "AU": "en-AU", "CA": "en-CA",
}


def locale_for_country(country_code: str | None) -> str:
    return _COUNTRY_LOCALES.get((country_code or "").upper(), "en-US")


def query_geo(*, local_port: int | None = None,
              proxy_url: str | None = None,
              timeout: float = 5.0) -> dict | None:
    """Fetch exit-IP geo through the given local forwarder port or proxy
    URL.  Returns None on any failure (fail-open)."""
    if local_port is not None:
        proxy = f"http://127.0.0.1:{local_port}"
    elif proxy_url is not None:
        proxy = proxy_url
    else:
        return None
    try:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        req = urllib.request.Request(_GEO_URL,
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.build_opener(handler).open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        if data.get("status") == "success" and data.get("timezone"):
            return data
    except Exception:
        pass
    return None


def align_profile(profile: FingerprintProfile, geo: dict) -> FingerprintProfile:
    """Fill unset timezone/language/geolocation/webrtc_ip from exit-IP geo;
    explicit values win."""
    p = profile
    if p.timezone is None and geo.get("timezone"):
        p = replace(p, timezone=geo["timezone"])
    if p.language is None:
        p = replace(p, language=locale_for_country(geo.get("countryCode")))
    if p.geolocation is None and geo.get("lat") is not None:
        p = replace(p, geolocation=(geo["lat"], geo["lon"]))
    if p.webrtc_ip is None and geo.get("query"):
        p = replace(p, webrtc_ip=geo["query"])
    return p
