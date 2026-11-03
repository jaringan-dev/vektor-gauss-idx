"""Dashboard access control (vg.web.auth): password gate, throttling, headers, and the unsafe-startup guard."""

import base64
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from vg.web import auth  # noqa: E402

PW = "correct-horse-battery"


def _basic(user="vg", password=PW):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def _client(password=PW, clock=None):
    app = FastAPI()

    @app.get("/")
    def home():
        return {"ok": True}

    @app.get("/healthz")
    def health():
        return {"ok": True}

    kwargs = {"clock": clock} if clock else {}
    app.add_middleware(auth.AuthMiddleware, creds=lambda: ("vg", password), **kwargs)
    return TestClient(app)


def test_request_without_credentials_gets_a_basic_challenge():
    r = _client().get("/")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic realm=")


def test_correct_credentials_pass_and_wrong_ones_do_not():
    c = _client()
    assert c.get("/", headers=_basic()).status_code == 200
    assert c.get("/", headers=_basic(password="wrong")).status_code == 401
    assert c.get("/", headers=_basic(user="someone")).status_code == 401
    assert c.get("/", headers={"Authorization": "Basic !!!not-base64!!!"}).status_code == 401
    assert c.get("/", headers={"Authorization": "Bearer abc"}).status_code == 401


def test_password_with_a_colon_is_handled():
    pw = "pass:with:colons-123"
    assert _client(pw).get("/", headers=_basic(password=pw)).status_code == 200


def test_healthz_is_open_for_monitors_but_nothing_else_is():
    c = _client()
    assert c.get("/healthz").status_code == 200 and c.get("/").status_code == 401


def test_every_response_carries_security_headers_including_the_challenge():
    for r in (_client().get("/"), _client().get("/", headers=_basic())):
        assert r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"


def test_without_a_password_it_only_adds_headers_and_never_blocks():
    c = _client(password="")
    r = c.get("/")
    assert r.status_code == 200 and r.headers["x-frame-options"] == "DENY"


def test_repeated_failures_lock_the_client_out_even_for_the_right_password_then_release():
    now = [1000.0]
    c = _client(clock=lambda: now[0])
    for _ in range(auth.MAX_FAILURES):
        assert c.get("/", headers=_basic(password="guess")).status_code == 401
    assert c.get("/", headers=_basic()).status_code == 429               # locked, even with the right password
    now[0] += auth.LOCKOUT + 1
    assert c.get("/", headers=_basic()).status_code == 200                # released after the lockout


def test_the_browsers_first_unauthenticated_request_does_not_count_as_a_failure():
    now = [0.0]
    c = _client(clock=lambda: now[0])
    for _ in range(auth.MAX_FAILURES + 3):
        assert c.get("/").status_code == 401
    assert c.get("/", headers=_basic()).status_code == 200


def test_failures_expire_outside_the_window():
    now = [0.0]
    c = _client(clock=lambda: now[0])
    for _ in range(auth.MAX_FAILURES - 1):
        c.get("/", headers=_basic(password="guess"))
    now[0] += auth.FAIL_WINDOW + 1
    c.get("/", headers=_basic(password="guess"))                          # old failures aged out: still only one
    assert c.get("/", headers=_basic()).status_code == 200


@pytest.mark.parametrize("host,password,ok", [
    ("127.0.0.1", "", True), ("localhost", "", True), ("::1", "", True), ("127.0.0.5", None, True),
    ("0.0.0.0", "", False), ("0.0.0.0", None, False), ("0.0.0.0", "short", False), ("100.64.1.2", "x" * 11, False),
    ("0.0.0.0", "x" * 12, True), ("192.168.1.20", "correct-horse-battery", True),
])
def test_startup_guard_refuses_to_listen_beyond_loopback_without_a_strong_password(host, password, ok):
    assert (auth.startup_error(host, password) is None) is ok


def test_the_real_app_is_wired_and_enforces_when_a_password_is_configured(monkeypatch):
    pytest.importorskip("psycopg")
    from vg.web import app as webapp

    assert any(m.cls is auth.AuthMiddleware for m in webapp.app.user_middleware)
    monkeypatch.setattr(webapp.CONFIG, "web_password", PW, raising=False)
    monkeypatch.setattr(webapp.CONFIG, "web_user", "vg", raising=False)
    c = TestClient(webapp.app)
    assert c.get("/guide").status_code == 401
    assert c.get("/healthz").status_code == 200
    assert c.get("/guide", headers=_basic()).status_code == 200


def test_serve_script_is_a_foreground_runner_that_reads_host_and_password_from_env():
    text = (Path(__file__).resolve().parent.parent / "scripts" / "serve.sh").read_text(encoding="utf-8")
    assert "exec python -m vg serve" in text and "VG_WEB_HOST" in text and "VG_WEB_PORT" in text
