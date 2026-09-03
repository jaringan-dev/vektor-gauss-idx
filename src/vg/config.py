"""Environment-driven configuration. Reads .env if present (via python-dotenv)."""

from __future__ import annotations

import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Config:
    database_url: str
    artifact_dir: Path
    http_user_agent: str
    http_timeout: float
    request_delay_seconds: float
    idx_base_url: str
    telegram_bot_token: str
    telegram_chat_id: str
    watchlist: tuple[str, ...]

    @staticmethod
    def load() -> "Config":
        wl = os.environ.get("VG_WATCHLIST", "")
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
            telegram_bot_token=os.environ.get("VG_TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("VG_TELEGRAM_CHAT_ID", ""),
            watchlist=tuple(t.strip().upper() for t in wl.split(",") if t.strip()),
        )


CONFIG = Config.load()
