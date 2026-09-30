"""Unit tests: profile generation coherence and flag building (no browser needed)."""

import pytest

from veilbrowser.profile import (
    _LOCALE_TIMEZONES,
    FingerprintProfile,
    PRESETS,
    from_preset,
    resolve_fingerprint,
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


class TestResolveFingerprint:
    def test_int_and_numeric_string_mean_seed(self):
        assert resolve_fingerprint(42).seed == 42
        assert resolve_fingerprint("42").seed == 42
        assert resolve_fingerprint(-7).seed == -7

    def test_saved_file_round_trip(self, tmp_path):
        f = tmp_path / "fp.json"
        FingerprintProfile(seed=42, platform="macos",
                           language="ja-JP").save(str(f))
        p = resolve_fingerprint(str(f))
        assert (p.seed, p.platform, p.language) == (42, "macos", "ja-JP")

    def test_missing_file_raises_with_both_meanings(self, tmp_path):
        with pytest.raises(ValueError, match="seed number nor"):
            resolve_fingerprint(str(tmp_path / "nope.json"))

    def test_rejects_non_numeric_gibberish(self):
        with pytest.raises(ValueError, match="neither"):
            resolve_fingerprint("hello world")


class TestFingerprintCLI:
    """--fingerprint wiring: seed | file, plus the fingerprint-save alias."""

    @staticmethod
    def _args(**kw):
        from veilbrowser.cli import _profile_from_args
        ns = dict(fingerprint=None, profile_file=None, preset=None, seed=None,
                  platform=None, timezone=None, language=None, brand=None,
                  concurrency=None, proxy=None)
        ns.update(kw)
        import argparse
        return _profile_from_args(argparse.Namespace(**ns))

    def test_launch_fingerprint_number(self):
        assert self._args(fingerprint="42").seed == 42

    def test_launch_fingerprint_file_with_override(self, tmp_path):
        f = tmp_path / "fp.json"
        FingerprintProfile(seed=99, platform="linux").save(str(f))
        p = self._args(fingerprint=str(f), timezone="Asia/Tokyo")
        assert p.seed == 99 and p.platform == "linux"
        assert p.timezone == "Asia/Tokyo"

    def test_bad_fingerprint_exits_cleanly(self, tmp_path):
        import argparse
        from veilbrowser.cli import _profile_from_args
        with pytest.raises(SystemExit, match="--fingerprint"):
            _profile_from_args(argparse.Namespace(
                fingerprint=str(tmp_path / "nope.json"), profile_file=None,
                preset=None, seed=None, platform=None, timezone=None,
                language=None, brand=None, concurrency=None, proxy=None))

    def test_fingerprint_save_alias_writes_json(self, tmp_path, capsys):
        from veilbrowser import cli
        out = tmp_path / "alias.json"
        assert cli.main(["fingerprint-save", "--seed", "5", str(out)]) == 0
        import json
        assert json.load(open(out))["seed"] == 5


class TestLaunchFingerprintArg:
    """launch(fingerprint=...) resolves before any kernel is needed."""

    def test_rejects_profile_and_fingerprint_together(self, tmp_path):
        from veilbrowser.browser import launch
        with pytest.raises(ValueError, match="not both"):
            launch(FingerprintProfile(seed=1), fingerprint=42)

    def test_bad_fingerprint_fails_before_binary_lookup(self, tmp_path):
        from veilbrowser.browser import launch
        with pytest.raises(ValueError, match="seed number nor"):
            launch(fingerprint=str(tmp_path / "nope.json"))

    def test_valid_fingerprint_reaches_binary_check(self, monkeypatch):
        import veilbrowser.browser as browser_mod
        from veilbrowser.browser import launch
        # hermetic: resolution succeeds, then the missing kernel stops launch
        monkeypatch.setattr(browser_mod, "default_binary", lambda vanilla=False: None)
        with pytest.raises(RuntimeError, match="binary not found"):
            launch(fingerprint=42)


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


class TestIdentityBinding:
    """Persistent-profile identity binding (no kernel binary needed)."""

    def _profile(self, seed, **kw):
        return FingerprintProfile(seed=seed, **kw).resolved()

    def test_first_call_writes_identity(self, tmp_path):
        from veilbrowser.browser import _bind_identity
        udd = str(tmp_path / "p")
        ident = _bind_identity(udd, self._profile(71, platform="windows"))
        assert ident["seed"] == 71
        assert ident["platform"] == "windows"
        import json
        import os
        with open(os.path.join(udd, "veil-identity.json")) as f:
            stored = json.load(f)
        assert stored == ident

    def test_same_identity_passes(self, tmp_path):
        from veilbrowser.browser import _bind_identity
        udd = str(tmp_path / "p")
        _bind_identity(udd, self._profile(72, platform="macos",
                                          language="en-US"))
        _bind_identity(udd, self._profile(72, platform="macos",
                                          language="en-US"))

    def test_different_identity_raises_with_fields(self, tmp_path):
        from veilbrowser.browser import IdentityMismatch, _bind_identity
        udd = str(tmp_path / "p")
        _bind_identity(udd, self._profile(73, platform="windows"))
        with pytest.raises(IdentityMismatch, match="seed"):
            _bind_identity(udd, self._profile(74, platform="windows"))
        with pytest.raises(IdentityMismatch, match="platform"):
            _bind_identity(udd, self._profile(73, platform="linux"))

    def test_rebind_replaces_identity(self, tmp_path):
        from veilbrowser.browser import _bind_identity
        udd = str(tmp_path / "p")
        _bind_identity(udd, self._profile(75, platform="windows"))
        ident = _bind_identity(udd, self._profile(76, platform="linux"),
                               rebind=True)
        assert ident["seed"] == 76
        # a subsequent non-rebind launch must accept the new identity
        _bind_identity(udd, self._profile(76, platform="linux"))

    def test_corrupt_identity_file_fails_open(self, tmp_path):
        from veilbrowser.browser import _bind_identity
        udd = str(tmp_path / "p")
        import os
        os.makedirs(udd, exist_ok=True)
        with open(os.path.join(udd, "veil-identity.json"), "w") as f:
            f.write("{not json")
        ident = _bind_identity(udd, self._profile(77))
        assert ident["seed"] == 77  # launched, and the file got rewritten
        with open(os.path.join(udd, "veil-identity.json")) as f:
            import json
            assert json.load(f)["seed"] == 77

    def test_geo_aligned_timezone_is_bound(self, tmp_path):
        # explicit timezone participates in the binding: relaunching the same
        # profile dir with a different tz must be a visible decision
        from veilbrowser.browser import IdentityMismatch, _bind_identity
        udd = str(tmp_path / "p")
        _bind_identity(udd, self._profile(78, timezone="Asia/Tokyo"))
        with pytest.raises(IdentityMismatch, match="timezone"):
            _bind_identity(udd, self._profile(78, timezone="Europe/Berlin"))
