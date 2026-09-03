import re
import time
from datetime import UTC

import pytest

from specter.core.clock import FrozenClock, SystemClock
from specter.core.ids import new_id
from specter.core.settings import Settings

ID_RE = re.compile(r"^tgt_[0-9a-f]{32}$")


class TestIds:
    def test_format(self) -> None:
        assert ID_RE.match(new_id("tgt"))

    def test_uniqueness(self) -> None:
        ids = {new_id("evt") for _ in range(10_000)}
        assert len(ids) == 10_000

    def test_time_ordered(self) -> None:
        ids = []
        for _ in range(20):
            ids.append(new_id("tgt"))
            time.sleep(0.005)
        assert ids == sorted(ids)

    @pytest.mark.parametrize("bad", ["", "TGT", "1x", "toolongprefixxxxxx", "has_underscore"])
    def test_rejects_bad_prefix(self, bad: str) -> None:
        with pytest.raises(ValueError):
            new_id(bad)


class TestClock:
    def test_system_clock_is_monotonic_and_utc(self) -> None:
        clock = SystemClock()
        assert clock.now() <= clock.now()
        assert clock.wall().tzinfo is UTC

    def test_frozen_clock_advances_both_hands(self) -> None:
        clock = FrozenClock(mono=100.0)
        before = clock.wall()
        clock.advance(30.0)
        assert clock.now() == 130.0
        assert (clock.wall() - before).total_seconds() == 30.0


class TestSettings:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.chdir(tmp_path)  # no .env / settings.toml here
        settings = Settings()
        assert settings.env == "local"
        assert settings.bus == "redis"
        assert settings.redis.url.startswith("redis://")
        assert settings.models.detector.device == "mps"

    def test_env_overrides_nested(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SPECTER_REDIS__URL", "redis://example:6379/3")
        monkeypatch.setenv("SPECTER_LOG__LEVEL", "DEBUG")
        settings = Settings()
        assert settings.redis.url == "redis://example:6379/3"
        assert settings.log.level == "DEBUG"

    def test_secret_is_not_reprd(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SPECTER_SECURITY__API_KEY", "super-secret")
        settings = Settings()
        assert "super-secret" not in repr(settings)
        assert settings.security.api_key.get_secret_value() == "super-secret"
