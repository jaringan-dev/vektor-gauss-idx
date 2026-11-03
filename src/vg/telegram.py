"""Minimal Telegram Bot API sender (sendMessage only)."""

from __future__ import annotations

import httpx

from .config import CONFIG

_LIMIT = 3800  # under Telegram's 4096, leaving room for entities


def _split(text: str, limit: int = _LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    out, buf = [], ""
    for para in text.split("\n\n"):
        block = (buf + "\n\n" + para) if buf else para
        if len(block) <= limit:
            buf = block
            continue
        if buf:
            out.append(buf)
        # a single paragraph over the limit — hard-split on lines
        buf = ""
        for line in para.split("\n"):
            if len(buf) + len(line) + 1 > limit:
                out.append(buf)
                buf = line
            else:
                buf = (buf + "\n" + line) if buf else line
    if buf:
        out.append(buf)
    return out


class TelegramSender:
    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        self.token = token or CONFIG.telegram_bot_token
        self.chat_id = chat_id or CONFIG.telegram_chat_id

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, parse_mode: str = "HTML", silent: bool = False) -> int:
        if not self.configured:
            raise RuntimeError(
                "VG_TELEGRAM_BOT_TOKEN / VG_TELEGRAM_CHAT_ID not set — use --dry-run to preview"
            )
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        chunks = _split(text)
        with httpx.Client(timeout=CONFIG.http_timeout) as client:
            for chunk in chunks:
                resp = client.post(
                    url,
                    json={
                        "chat_id": self.chat_id,
                        "text": chunk,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                        "disable_notification": silent,
                    },
                )
                resp.raise_for_status()
        return len(chunks)
