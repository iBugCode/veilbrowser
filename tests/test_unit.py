"""Unit tests: profile generation coherence and flag building (no browser needed)."""

import pytest

from veilbrowser.profile import (
    _LOCALE_TIMEZONES,
    FingerprintProfile,
    PRESETS,
    from_preset,
)
from veilbrowser.proxy import LocalForwarder, UpstreamProxy, parse_proxy_url


class TestProfileResolution:
    def test_deterministic_for_same_seed(self):
        a = FingerprintProfile(seed=1234).resolved()
        b = FingerprintProfile(seed=1234).resolved()
        assert a == b

    def test_resolved_fills_all_coherence_fields(self):
        p = FingerprintProfile(seed=42).resolved()
        assert p.platform in ("windows", "macos", "linux")
        assert p.language in ("en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "pt-BR",
                              "zh-CN", "ja-JP", "ko-KR", "ru-RU", "en-IN")
        assert p.timezone
        assert p.hardware_concurrency >= 2
        assert p.brand in ("Chrome", "Edge", "Opera", "Vivaldi")

    def test_timezone_consistent_with_language(self):
        for seed in range(50):
            p = FingerprintProfile(seed=seed).resolved()
            assert p.timezone in _LOCALE_TIMEZONES[p.language], (
                f"seed {seed}: tz {p.timezone} contradicts lang {p.language}")

    def test_explicit_overrides_survive_resolution(self):
        p = FingerprintProfile(seed=1, language="ja-JP").resolved()
        assert p.language == "ja-JP"
        assert p.timezone in _LOCALE_TIMEZONES["ja-JP"]

    def test_different_seeds_vary(self):
        values = {FingerprintProfile(seed=s).resolved().hardware_concurrency
                  for s in range(30)}
        assert len(values) > 1

    def test_accept_language_format(self):
        assert FingerprintProfile(seed=1, language="en-US").accept_language() == "en-US,en"
        assert FingerprintProfile(seed=1, language="de-DE").accept_language() == "de-DE,de"


class TestFlagBuilding:
    def test_seed_flag_always_present(self):
        flags = FingerprintProfile(seed=7).fingerprint_flags()
        assert "--fingerprint=7" in flags

    def test_full_flag_set(self):
        p = FingerprintProfile(seed=7, platform="windows", brand="Edge",
                               brand_version="148.0.0.0", timezone="Asia/Shanghai",
                               language="zh-CN", hardware_concurrency=16)
        flags = p.fingerprint_flags()
        assert "--fingerprint-platform=windows" in flags
        assert "--fingerprint-brand=Edge" in flags
        assert "--fingerprint-brand-version=148.0.0.0" in flags
        assert "--timezone=Asia/Shanghai" in flags
        assert "--lang=zh-CN" in flags
        assert "--accept-lang=zh-CN,zh" in flags
        assert "--fingerprint-hardware-concurrency=16" in flags

    def test_disable_spoofing_flag(self):
        p = FingerprintProfile(seed=7, disable_spoofing=("canvas", "gpu"))
        assert "--disable-spoofing=canvas,gpu" in p.fingerprint_flags()


class TestPresets:
    def test_known_presets_exist(self):
        assert {"windows-us-office", "macos-us-designer", "linux-dev-de"} <= set(PRESETS)

    def test_from_preset_resolved_and_coherent(self):
        p = from_preset("windows-us-office", seed=99)
        assert p.platform == "windows"
        assert p.timezone in _LOCALE_TIMEZONES[p.language]

    def test_unknown_preset_raises(self):
        with pytest.raises(KeyError):
            from_preset("no-such-preset", seed=1)


class TestProxyParsing:
    def test_http_with_creds(self):
        up = parse_proxy_url("http://alice:s3cret@1.2.3.4:8080")
        assert up == UpstreamProxy("http", "1.2.3.4", 8080, "alice", "s3cret")
        assert up.has_auth

    def test_socks5h_normalizes_to_socks5(self):
        up = parse_proxy_url("socks5h://bob@5.6.7.8:1080")
        assert up.scheme == "socks5"
        assert up.username == "bob" and up.password is None

    @pytest.mark.parametrize("bad", [
        "ftp://1.2.3.4:21", "http://noport", "1.2.3.4:8080", "http://host:notaport",
    ])
    def test_invalid_urls_rejected(self, bad):
        with pytest.raises(Exception):
            parse_proxy_url(bad)


class TestLocalForwarder:
    def test_chrome_arg_scheme_matches_upstream(self):
        for scheme, expected_prefix in (("socks5", "socks5://"), ("http", "http://")):
            up = UpstreamProxy(scheme, "127.0.0.1", 19999, "u", "p")
            fwd = LocalForwarder(up)
            try:
                arg = fwd.chrome_proxy_arg()
                assert arg.startswith(expected_prefix)
                assert arg.endswith(f"127.0.0.1:{fwd.local_port}")
            finally:
                fwd.stop()

    def test_stop_releases_port(self):
        fwd = LocalForwarder(UpstreamProxy("socks5", "127.0.0.1", 1))
        port = fwd.local_port
        fwd.stop()
        # listener must be gone: SO_REUSEADDR allows rebinding TIME_WAIT sockets
        # but never an active LISTEN socket, so a successful bind proves closure
        import socket
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.close()
