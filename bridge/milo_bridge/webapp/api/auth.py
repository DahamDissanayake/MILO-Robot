"""Login/logout endpoints for the dashboard's session cookie."""

from __future__ import annotations

from aiohttp import web

from ..auth import verify_password

SESSION_COOKIE = "milo_session"


def _client_ip(request: web.Request) -> str:
    peername = request.transport.get_extra_info("peername") if request.transport else None
    return peername[0] if peername else "unknown"


async def post_login(request: web.Request) -> web.Response:
    app = request.app
    deps = app["deps"]
    throttle = app["login_throttle"]
    ip = _client_ip(request)
    if not throttle.allow(ip):
        return web.json_response({"error": "too many attempts, try again shortly"}, status=429)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid request"}, status=400)
    username = str(body.get("username", ""))
    password = str(body.get("password", ""))
    ok = username == deps.config.web_username and verify_password(password, deps.config.web_password_hash)
    if not ok:
        throttle.record_failure(ip)
        return web.json_response({"error": "invalid credentials"})
    throttle.record_success(ip)
    token = app["sessions"].create(username)
    resp = web.json_response({"ok": True})
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Strict", path="/")
    return resp


async def post_logout(request: web.Request) -> web.Response:
    app = request.app
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        app["sessions"].revoke(token)
    resp = web.json_response({"ok": True})
    resp.del_cookie(SESSION_COOKIE, path="/")
    return resp


def register(app: web.Application) -> None:
    app.router.add_post("/api/login", post_login)
    app.router.add_post("/api/logout", post_logout)
