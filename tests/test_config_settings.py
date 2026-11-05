"""Settings (pydantic-settings): typed, validated, and loud about a malformed value (no DB, no network).

The pre-existing parsing contract (radar tags, forward_since, CSV tickers) stays in test_config.py."""

from __future__ import annotations

import pytest

from vg.config import Config, ConfigError, Settings

_ALL = (
    "VG_DATABASE_URL", "VG_HTTP_TIMEOUT", "VG_REQUEST_DELAY_SECONDS", "VG_FEED_DELAY_SECONDS", "VG_IDX_BASE_URL",
    "VG_FEED_BASE_URL", "VG_FEED_API_KEY", "VG_HEALTHCHECK_URL", "VG_ACCOUNT_BALANCE", "VG_RISK_PCT",
    "VG_FEE_PCT", "VG_ATR_SL_MULT", "VG_TARGET_R", "VG_WEB_USER", "VG_WEB_PASSWORD", "VG_DIGEST_TRADE_CALLS",
    "VG_HARVEST_TICKERS", "VG_RADAR_TAGS", "VG_FORWARD_SINCE", "VG_WATCHLIST", "VG_HOLDINGS",
)


def _fresh(monkeypatch, **env) -> Settings:
    for k in _ALL:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings.load()


def test_config_is_the_settings_class():
    assert Config is Settings


def test_defaults_match_the_pre_pydantic_values(monkeypatch):
    c = _fresh(monkeypatch)
    assert c.database_url == "postgresql://vg:vg@localhost:5432/vektorgauss"
    assert (c.http_timeout, c.request_delay_seconds, c.feed_delay_seconds) == (30.0, 4.0, 1.0)
    assert c.idx_base_url == "https://www.idx.co.id" and c.feed_base_url == "" and c.healthcheck_url == ""
    assert c.digest_trade_calls is True and c.account_balance == 0.0
    assert c.web_user == "vg" and c.web_password == ""
    assert (c.atr_sl_mult, c.target_r) == (2.0, 2.5)


def test_percent_settings_become_fractions_including_the_defaults(monkeypatch):
    assert _fresh(monkeypatch).risk_pct == pytest.approx(0.01)
    assert _fresh(monkeypatch).fee_pct == pytest.approx(0.003)
    c = _fresh(monkeypatch, VG_RISK_PCT="2.5", VG_FEE_PCT="0.4")
    assert c.risk_pct == pytest.approx(0.025) and c.fee_pct == pytest.approx(0.004)


def test_blank_values_mean_unset_like_the_env_example(monkeypatch):
    c = _fresh(monkeypatch, VG_ACCOUNT_BALANCE="", VG_FORWARD_SINCE="", VG_FEED_BASE_URL="", VG_HTTP_TIMEOUT="")
    assert c.account_balance == 0.0 and c.forward_since is None and c.http_timeout == 30.0


def test_tickers_are_csv_upper_stripped_and_empty_by_default(monkeypatch):
    c = _fresh(monkeypatch, VG_WATCHLIST="bbri, antm ,", VG_HOLDINGS="bbri")
    assert c.watchlist == ("BBRI", "ANTM") and c.holdings == ("BBRI",) and c.harvest_tickers == ()


def test_digest_trade_calls_accepts_the_usual_spellings(monkeypatch):
    assert _fresh(monkeypatch, VG_DIGEST_TRADE_CALLS="false").digest_trade_calls is False
    assert _fresh(monkeypatch, VG_DIGEST_TRADE_CALLS="yes").digest_trade_calls is True
    assert _fresh(monkeypatch, VG_DIGEST_TRADE_CALLS="1").digest_trade_calls is True


def test_urls_lose_a_trailing_slash_and_must_be_http(monkeypatch):
    c = _fresh(monkeypatch, VG_FEED_BASE_URL="https://feed.example.com/", VG_HEALTHCHECK_URL="https://hc-ping.com/abc")
    assert c.feed_base_url == "https://feed.example.com"
    with pytest.raises(ConfigError, match="VG_FEED_BASE_URL"):
        _fresh(monkeypatch, VG_FEED_BASE_URL="feed.example.com")
    with pytest.raises(ConfigError, match="VG_HEALTHCHECK_URL"):
        _fresh(monkeypatch, VG_HEALTHCHECK_URL="ftp://x")


def test_database_url_accepts_a_url_or_a_libpq_string_only(monkeypatch):
    assert _fresh(monkeypatch, VG_DATABASE_URL="postgres://u:p@h/db").database_url == "postgres://u:p@h/db"
    assert _fresh(monkeypatch, VG_DATABASE_URL="host=h dbname=d").database_url == "host=h dbname=d"
    for bad in ("mysql://u@h/db", "just-a-word"):
        with pytest.raises(ConfigError, match="VG_DATABASE_URL"):
            _fresh(monkeypatch, VG_DATABASE_URL=bad)


def test_bad_numbers_fail_with_the_variable_name_not_a_traceback(monkeypatch):
    with pytest.raises(ConfigError) as ei:
        _fresh(monkeypatch, VG_HTTP_TIMEOUT="-5", VG_RISK_PCT="abc")
    msg = str(ei.value)
    assert "VG_HTTP_TIMEOUT" in msg and "VG_RISK_PCT" in msg and "'-5'" in msg


def test_secrets_are_hidden_from_repr(monkeypatch):
    c = _fresh(monkeypatch, VG_FEED_API_KEY="s3cr3t-key", VG_WEB_PASSWORD="hunter2hunter2")
    assert c.feed_api_key == "s3cr3t-key"
    assert "s3cr3t-key" not in repr(c) and "hunter2hunter2" not in repr(c)


def test_blank_web_user_falls_back_to_default(monkeypatch):
    assert _fresh(monkeypatch, VG_WEB_USER="   ").web_user == "vg"


def test_attributes_stay_assignable_for_test_monkeypatching(monkeypatch):
    c = _fresh(monkeypatch)
    monkeypatch.setattr(c, "feed_delay_seconds", 0.25)
    assert c.feed_delay_seconds == 0.25
