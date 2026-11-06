"""Access control for the dashboard (RENCANA-UI-UX §3.6 / checklist I3): HTTP Basic auth + security headers.

The dashboard shows a personal watchlist and holdings, so it must never be reachable without a password once it listens
on anything other than the local machine. Rules:

  * `vg serve --host <non-loopback>` REFUSES to start unless VG_WEB_PASSWORD is set and long enough (`startup_error`);
  * with a password set, every request (except /healthz) needs HTTP Basic credentials; comparisons are constant-time;
  * repeated failures from one client address are throttled (HTTP 429) so a password cannot be guessed at speed;
  * all responses carry no-store / no-sniff / no-frame headers and `Referrer-Policy: same-origin` (nothing is leaked to another site, but
    a browser can still label a form post from this dashboard with its own address, which the watchlist forms check; `no-referrer`
    made it send `Origin: null` for every post, so the dashboard could not tell its own forms from a stranger's).

Basic auth sends the password in every request, so it must travel over an encrypted path (Tailscale/WireGuard, or HTTPS
from a tunnel). The setup guide (PHONE-ACCESS.md) says so, and recommends the encrypted route first.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import time
from collections import defaultdict, deque
from collections.abc import Callable

MIN_PASSWORD_LEN = 12
EXEMPT_PATHS = frozenset({"/healthz"})
MAX_FAILURES = 8          # failures allowed per client within FAIL_WINDOW before it is locked out
FAIL_WINDOW = 300.0       # seconds
LOCKOUT = 600.0           # seconds

_SECURITY_HEADERS = [
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
]


def is_loopback(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("localhost", "::1", "[::1]") or h.startswith("127.")


def startup_error(host: str, password: str | None) -> str | None:
    """A message when serving on `host` with this password would be unsafe, else None."""
    if is_loopback(host):
        return None
    if not password:
        return (f"Refusing to listen on {host}: set VG_WEB_PASSWORD (at least {MIN_PASSWORD_LEN} characters) in .env first. "
                "The dashboard shows your watchlist and holdings and must not be open without a password.")
    if len(password) < MIN_PASSWORD_LEN:
        return f"VG_WEB_PASSWORD is too short ({len(password)} characters); use at least {MIN_PASSWORD_LEN}."
    return None


def _parse_basic(header: str | None) -> tuple[str, str] | None:
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        raw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    user, sep, password = raw.partition(":")
    return (user, password) if sep else None


def check_credentials(header: str | None, user: str, password: str) -> bool:
    """Constant-time comparison of both parts (no early exit on the first mismatch)."""
    got = _parse_basic(header)
    if got is None:
        return False
    ok_user = hmac.compare_digest(got[0].encode(), user.encode())
    ok_pass = hmac.compare_digest(got[1].encode(), password.encode())
    return ok_user and ok_pass


class AuthMiddleware:
    """ASGI middleware. `creds()` is read per request, so configuration changes (and tests) take effect immediately.
    No password configured -> only the security headers are added (loopback-only use)."""

    def __init__(self, app, creds: Callable[[], tuple[str, str]], clock: Callable[[], float] = time.monotonic):
        self.app = app
        self.creds = creds
        self.clock = clock
        self._fails: dict[str, deque] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}

    # -- throttle ---------------------------------------------------------------------------------------------
    def _locked(self, who: str) -> bool:
        until = self._locked_until.get(who)
        if until is None:
            return False
        if self.clock() >= until:
            del self._locked_until[who]
            self._fails.pop(who, None)
            return False
        return True

    def _fail(self, who: str) -> None:
        now = self.clock()
        q = self._fails[who]
        q.append(now)
        while q and now - q[0] > FAIL_WINDOW:
            q.popleft()
        if len(q) >= MAX_FAILURES:
            self._locked_until[who] = now + LOCKOUT

    # -- ASGI -------------------------------------------------------------------------------------------------
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + _SECURITY_HEADERS
            await send(message)

        user, password = self.creds()
        if not password or scope.get("path") in EXEMPT_PATHS:
            await self.app(scope, receive, send_with_headers)
            return

        who = (scope.get("client") or ("unknown", 0))[0]
        if self._locked(who):
            await self._respond(send_with_headers, 429, b"Too many failed attempts. Try again later.", [(b"retry-after", b"600")])
            return
        header = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"authorization"), None)
        if check_credentials(header, user, password):
            self._fails.pop(who, None)
            await self.app(scope, receive, send_with_headers)
            return
        if header is not None:                     # a missing header is just the browser's first, unauthenticated request
            self._fail(who)
        await self._respond(send_with_headers, 401, b"Authentication required.",
                            [(b"www-authenticate", b'Basic realm="Vektor Gauss", charset="UTF-8"')])

    @staticmethod
    async def _respond(send, status: int, body: bytes, extra: list) -> None:
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())] + extra})
        await send({"type": "http.response.body", "body": body})
