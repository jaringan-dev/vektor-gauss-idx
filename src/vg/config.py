"""Environment-driven configuration. Reads .env if present (via python-dotenv)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

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
DEFAULT_RADAR_TAGS = ("FLOAT-ASING-PADAT", "TIKET-BESAR-AKUM")


def _parse_date(s: str | None) -> date | None:
    if not s or not s.strip():
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None


@dataclass
class Config:
    database_url: str
    artifact_dir: Path
    http_user_agent: str
    http_timeout: float
    request_delay_seconds: float
    idx_base_url: str
    arjum_base_url: str
    arjum_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    watchlist: tuple[str, ...]
    holdings: tuple[str, ...]
    radar_tags: tuple[str, ...]
    forward_since: date | None
    digest_trade_calls: bool
    account_balance: float
    risk_pct: float
    fee_pct: float
    atr_sl_mult: float
    target_r: float

    @staticmethod
    def load() -> "Config":
        wl = os.environ.get("VG_WATCHLIST", "")
        hd = os.environ.get("VG_HOLDINGS", "")
        rt = os.environ.get("VG_RADAR_TAGS", "").strip()
        return Config(
            database_url=os.environ.get(
                "VG_DATABASE_URL",
                "postgresql://vg:vg@localhost:5432/vektorgauss",
            ),
            artifact_dir=Path(os.environ.get("VG_ARTIFACT_DIR", "./data/artifacts")),
            http_user_agent=os.environ.get("VG_HTTP_USER_AGENT", DEFAULT_UA),
            http_timeout=float(os.environ.get("VG_HTTP_TIMEOUT", "30")),
            request_delay_seconds=float(os.environ.get("VG_REQUEST_DELAY_SECONDS", "4")),
            idx_base_url=os.environ.get("VG_IDX_BASE_URL", "https://www.idx.co.id").rstrip("/"),
            # Broker-summary vendor (Phase 1.5, Tier 3). Endpoint + key both live in
            # .env, never in source — set VG_ARJUM_BASE_URL and VG_ARJUM_API_KEY.
            arjum_base_url=os.environ.get("VG_ARJUM_BASE_URL", "").rstrip("/"),
            arjum_api_key=os.environ.get("VG_ARJUM_API_KEY", ""),
            telegram_bot_token=os.environ.get("VG_TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("VG_TELEGRAM_CHAT_ID", ""),
            watchlist=tuple(t.strip().upper() for t in wl.split(",") if t.strip()),
            holdings=tuple(t.strip().upper() for t in hd.split(",") if t.strip()),
            radar_tags=(
                tuple(t.strip().upper() for t in rt.split(",") if t.strip())
                if rt else DEFAULT_RADAR_TAGS
            ),
            forward_since=_parse_date(os.environ.get("VG_FORWARD_SINCE")),
            digest_trade_calls=os.environ.get("VG_DIGEST_TRADE_CALLS", "true").lower()
            in ("1", "true", "yes"),
            account_balance=float(os.environ.get("VG_ACCOUNT_BALANCE", "0") or "0"),
            risk_pct=float(os.environ.get("VG_RISK_PCT", "1.0")) / 100.0,
            fee_pct=float(os.environ.get("VG_FEE_PCT", "0.3")) / 100.0,
            atr_sl_mult=float(os.environ.get("VG_ATR_SL_MULT", "2.0")),
            target_r=float(os.environ.get("VG_TARGET_R", "2.5")),
        )


CONFIG = Config.load()
