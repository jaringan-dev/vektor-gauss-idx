"""Environment-driven configuration, validated with pydantic-settings (Pydantic v2).

Every setting is read from a `VG_<NAME>` environment variable (e.g. `database_url` <- `VG_DATABASE_URL`). A `.env`
file is loaded into the process environment first (python-dotenv, never overriding a variable that is already set),
so the shell, cron/Task Scheduler and `.env` keep exactly the precedence they had before this module was typed.

Why python-dotenv instead of pydantic's own `env_file=`: several modules also read `os.environ` directly
(`VG_FEED_WAIT_MINUTES`, `VG_WEB_HOST`, ...), and tests mask a setting with `monkeypatch.delenv`. A pydantic-only
`env_file` would neither populate `os.environ` for those readers nor be maskable by a test.

Failure mode: a malformed value raises `ConfigError` at import time with one line per bad variable
("VG_HTTP_TIMEOUT: must be greater than 0 (got '-5')") instead of a raw pydantic traceback. Secrets are never echoed.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

try:  # optional
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Which probation tags the digest RADAR section surfaces (names outside the
# watchlist). The old default was AKUM-ASING; the first `vg eval` read (HANDOFF)
# showed it has no edge, so the default is now the two candidates that do.
DEFAULT_RADAR_TAGS = ("FLOAT-ASING-PADAT", "TIKET-BESAR-AKUM", "BROKER-ASING-AKUM", "BANDAR-CANDIDATE")

# Settings whose values must never appear in an error message.
_SECRETS = frozenset({"feed_api_key", "telegram_bot_token", "web_password"})

# pydantic-settings would otherwise try to JSON-decode a tuple from the environment ("BBRI,ANTM" is not JSON).
_Csv = Annotated[tuple[str, ...], NoDecode]


class ConfigError(ValueError):
    """One or more `VG_*` settings are invalid. The message lists each offending variable."""


def _parse_date(s: str | None) -> date | None:
    """Lenient ISO date: blank or malformed -> None (the forward-test clock is then simply 'not set')."""
    if not s or not s.strip():
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None


def _csv_upper(v: Any) -> tuple[str, ...]:
    """'bbri, antm' / ['bbri', 'antm'] -> ('BBRI', 'ANTM'); None / blank -> ()."""
    if v is None:
        return ()
    parts = v.split(",") if isinstance(v, str) else list(v)
    return tuple(str(t).strip().upper() for t in parts if str(t).strip())


def _http_url(v: str, *, field: str, allow_blank: bool) -> str:
    v = v.strip().rstrip("/")
    if not v:
        if allow_blank:
            return ""
        raise ValueError(f"{field} must not be blank")
    if not v.lower().startswith(("http://", "https://")):
        raise ValueError("must start with http:// or https://")
    return v


class Settings(BaseSettings):
    """All runtime settings. Instantiate via `Settings.load()` (what `CONFIG` is)."""

    model_config = SettingsConfigDict(
        env_prefix="VG_",
        case_sensitive=False,
        extra="ignore",          # other VG_* variables exist (VG_WEB_HOST, VG_BACKUP_*, ...) and are read elsewhere
        env_ignore_empty=True,   # `VG_FORWARD_SINCE=` (blank line in .env.example) means "unset", not an error
        validate_default=True,   # defaults run through the validators too (risk_pct/fee_pct are converted below)
    )

    # --- storage ------------------------------------------------------------------------
    database_url: str = "postgresql://vg:vg@localhost:5432/vektorgauss"
    artifact_dir: Path = Path("./data/artifacts")

    # --- HTTP ---------------------------------------------------------------------------
    http_user_agent: str = DEFAULT_UA
    http_timeout: float = Field(30.0, gt=0, le=600)
    request_delay_seconds: float = Field(4.0, ge=0)   # IDX sits behind Cloudflare and needs the slow delay
    # The VG feed has no Cloudflare. 1.0s was exercised for ~19k calls (2026-10-16/31) without a single throttle.
    feed_delay_seconds: float = Field(1.0, ge=0)
    # Wall-clock cap for the EOD done-details harvest (it runs before the second compute, so it delays the digest).
    # No new ticker is started once it is spent. 0 = no cap.
    done_budget_minutes: float = Field(15.0, ge=0)
    idx_base_url: str = "https://www.idx.co.id"
    # VG data feed (Phase 1.5, Tier 3): broker summary + Cloudflare-free price history.
    # Endpoint + key live in .env only, never in source.
    feed_base_url: str = ""
    feed_api_key: str = Field("", repr=False)
    # Dead-man's-switch ping URL (scripts/eod.sh reads the same variable from the shell).
    healthcheck_url: str = ""

    # --- Telegram -----------------------------------------------------------------------
    telegram_bot_token: str = Field("", repr=False)
    telegram_chat_id: str = ""

    # --- universe -----------------------------------------------------------------------
    watchlist: _Csv = ()
    holdings: _Csv = ()
    # Extra tickers to harvest VG-feed data for beyond watchlist+holdings (broker summary/inventory,
    # done-by-price). Widens the "panen" without code.
    harvest_tickers: _Csv = ()
    # Constituents of the LQ45 index, supplied by the operator (IDX republishes the list every February and
    # August; the repo stores only the index VALUE, never its members, so nothing is hard-coded here). Empty =
    # the done-details harvest covers the watchlist only. Order: watchlist first, then these.
    lq45_tickers: _Csv = ()
    radar_tags: _Csv = DEFAULT_RADAR_TAGS
    forward_since: date | None = None
    digest_trade_calls: bool = True

    # --- trade-call maths. VG_RISK_PCT / VG_FEE_PCT are written as PERCENT in .env; the attributes hold FRACTIONS ---
    account_balance: float = Field(0.0, ge=0)
    risk_pct: float = Field(1.0, gt=0, le=100)
    fee_pct: float = Field(0.3, ge=0, lt=100)
    atr_sl_mult: float = Field(2.0, gt=0)
    target_r: float = Field(2.5, gt=0)

    # --- dashboard access (web/auth.py). Empty password = loopback-only; required to listen elsewhere. ---
    web_user: str = "vg"
    web_password: str = Field("", repr=False)

    # ---------------------------------------------------------------- validators

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        if "://" in v:
            scheme = v.split("://", 1)[0].lower()
            if scheme not in ("postgresql", "postgres"):
                raise ValueError("must be a postgresql:// URL (or a libpq 'key=value' string)")
        elif "=" not in v:
            raise ValueError("must be a postgresql:// URL (or a libpq 'key=value' string)")
        return v

    @field_validator("idx_base_url")
    @classmethod
    def _check_idx_base_url(cls, v: str) -> str:
        return _http_url(v, field="idx_base_url", allow_blank=False)

    @field_validator("feed_base_url", "healthcheck_url")
    @classmethod
    def _check_optional_url(cls, v: str) -> str:
        return _http_url(v, field="url", allow_blank=True)

    @field_validator("watchlist", "holdings", "harvest_tickers", "lq45_tickers", mode="before")
    @classmethod
    def _split_tickers(cls, v: Any) -> tuple[str, ...]:
        return _csv_upper(v)

    @field_validator("radar_tags", mode="before")
    @classmethod
    def _split_radar(cls, v: Any) -> tuple[str, ...]:
        # A blank value means "use the default", never "no RADAR".
        return _csv_upper(v) or DEFAULT_RADAR_TAGS

    @field_validator("forward_since", mode="before")
    @classmethod
    def _lenient_date(cls, v: Any) -> date | None:
        # Kept lenient on purpose (an existing, tested contract): a malformed date means "forward test not set".
        return _parse_date(v) if isinstance(v, str) else v

    @field_validator("web_user", mode="before")
    @classmethod
    def _web_user_default(cls, v: Any) -> str:
        return (v.strip() if isinstance(v, str) else v) or "vg"

    @field_validator("risk_pct", "fee_pct")
    @classmethod
    def _percent_to_fraction(cls, v: float) -> float:
        return v / 100.0

    # ---------------------------------------------------------------- loading

    @classmethod
    def load(cls) -> Settings:
        """Read the environment now. Raises `ConfigError` (one line per bad variable) instead of a raw traceback."""
        try:
            return cls()
        except ValidationError as exc:
            raise ConfigError(_explain(exc)) from None


def _explain(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        name = str(err["loc"][0]) if err["loc"] else "?"
        got = "" if name in _SECRETS else f" (got {err['input']!r})"
        msg = str(err["msg"]).removeprefix("Value error, ")
        lines.append(f"VG_{name.upper()}: {msg}{got}")
    return "invalid configuration:\n  " + "\n  ".join(lines)


# Back-compat: `Config` / `Config.load()` is what the rest of the code base and the tests import.
Config = Settings

CONFIG = Settings.load()
