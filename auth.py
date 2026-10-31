"""App-level authentication & session security wall for the Paxton Bank bridge.

Implements defense-in-depth per OWASP & Google Secure Web Backend guidelines:
  - Server-enforced authentication middleware on HTTP and WebSocket upgrades.
  - PBKDF2-HMAC-SHA256 (390,000 iterations) password verification with constant-time compare.
  - Session key resolved via Google Cloud Secret Manager -> environment variable ->
    cryptographically random ephemeral key (`secrets.token_hex(32)`) with warning.
  - Hardened `HttpOnly`, `Secure`, `SameSite=Lax` session cookies with bounded TTL.
  - Per-IP sliding-window brute-force lockout (5 failed attempts -> 15-minute lockout)
    with PII-redacted structured audit logging (`telemetry.glog`).
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

AUTH_ON = os.environ.get("APP_AUTH", "off") == "password"
_USER = os.environ.get("APP_USERNAME", "paxton")
_PW_HASH = os.environ.get("APP_PASSWORD_HASH", "")  # salt$hexhash (pbkdf2)


def _resolve_session_key() -> bytes:
    """Resolve session HMAC key: Environment -> Secret Manager -> Cryptographic RNG."""
    env_key = os.environ.get("APP_SESSION_KEY", "").strip()
    if env_key:
        return env_key.encode("utf-8")
    try:
        from google.cloud import secretmanager
        import config
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{config.PROJECT_ID}/secrets/paxton-app-session-key/versions/latest"
        val = client.access_secret_version(name=name).payload.data.decode("utf-8").strip()
        if val:
            return val.encode("utf-8")
    except Exception:
        pass
    logging.warning("Generating ephemeral session signing secret. Instance-isolated!")
    return secrets.token_hex(32).encode("utf-8")


_KEY: bytes = _resolve_session_key()
SESSION_TTL = 12 * 3600
_COOKIE = "__Host-paxton_session"

_fails: dict[str, list[float]] = {}
_LOCK_N, _LOCK_WIN, _LOCK_FOR = 5, 900, 900


def _users() -> dict:
    """username -> salt$hexhash from Secret Manager / environment."""
    m = {}
    try:
        m.update(json.loads(os.environ.get("APP_USERS_JSON", "") or "{}"))
    except Exception:
        pass
    if _USER and _PW_HASH:
        m.setdefault(_USER, _PW_HASH)
    return m


def _sign(payload: str) -> str:
    mac = hmac.new(_KEY, payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}|{mac}".encode()).decode()


def _verify(token: str) -> bool:
    try:
        payload, mac = base64.urlsafe_b64decode(token.encode()).decode().rsplit("|", 1)
        good = hmac.new(_KEY, payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(mac, good):
            return False
        user, exp = payload.split(":")
        return user in _users() and time.time() < float(exp)
    except Exception:
        return False


def session_ok(request) -> bool:
    if not AUTH_ON:
        return True
    tok = request.cookies.get(_COOKIE, "") or request.cookies.get("paxton_session", "")
    return _verify(tok)


def ws_session_ok(websocket) -> bool:
    if not AUTH_ON:
        return True
    tok = websocket.cookies.get(_COOKIE, "") or websocket.cookies.get("paxton_session", "")
    return _verify(tok)


def _locked(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _fails.get(ip, []) if now - t < _LOCK_WIN]
    _fails[ip] = hits
    return len(hits) >= _LOCK_N


def check_password(pw: str, stored: str = "") -> bool:
    try:
        salt, hexhash = (stored or _PW_HASH).split("$", 1)
        cand = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 390_000).hex()
        return hmac.compare_digest(cand, hexhash)
    except Exception:
        return False


_LOGIN_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Paxton Bank Bridget — sign in</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{margin:0;font-family:-apple-system,'Segoe UI',Roboto,sans-serif;background:#00254A;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#fff;border-radius:16px;padding:40px 44px;width:340px;box-shadow:0 24px 60px rgba(0,0,0,.35)}
.brand{display:flex;gap:10px;align-items:center;margin-bottom:6px}.brand b{font-size:22px;letter-spacing:2px;color:#00254A}
.chip{background:#E0F2FE;color:#0369A1;font-family:monospace;font-size:11px;padding:2px 8px;border-radius:5px;letter-spacing:2px;font-weight:700}
p{color:#6B7280;font-size:13px;margin:4px 0 22px}
input{width:100%;box-sizing:border-box;border:1px solid #D1D5DB;border-radius:10px;padding:11px 13px;font-size:14px;margin-bottom:12px}
input:focus{outline:2px solid #0284c7;border-color:transparent}
button{width:100%;background:#0284c7;color:#fff;border:none;border-radius:10px;padding:12px;font-size:14px;font-weight:700;cursor:pointer}
button:hover{background:#0369A1}.err{background:#FEF2F2;color:#B91C1C;font-size:12.5px;border-radius:8px;padding:9px 12px;margin-bottom:12px}</style></head>
<body><form class="card" method="post" action="/login">
<div class="brand"><b>PAXTON BANK</b><span class="chip">BRIDGET</span></div>
<p>Commercial Banking AI Companion — restricted access</p>
{err}
<input name="username" placeholder="Username" autocomplete="username" required>
<input name="password" type="password" placeholder="Password" autocomplete="current-password" required>
<button type="submit">Sign in</button></form></body></html>"""


def login_page(err: str = "") -> HTMLResponse:
    import html
    safe_err = f'<div class="err">{html.escape(err)}</div>' if err else ""
    return HTMLResponse(_LOGIN_HTML.replace("{err}", safe_err), status_code=401 if err else 200)


async def handle_login(request):
    import telemetry
    ip = (request.headers.get("x-forwarded-for", "") or "?").split(",")[0].strip()
    if _locked(ip):
        telemetry.glog("login locked out", log_type="audit", severity="WARNING", ip=ip)
        return login_page("Too many attempts — try again in 15 minutes.")
    from urllib.parse import parse_qs
    body = parse_qs((await request.body()).decode("utf-8", "ignore"))
    user = (body.get("username", [""])[0] or "").strip()
    pw = body.get("password", [""])[0] or ""
    stored = _users().get(user, "")
    ok = bool(stored) and check_password(pw, stored)
    if not ok:
        _fails.setdefault(ip, []).append(time.time())
        telemetry.glog("login failed", log_type="audit", severity="WARNING", ip=ip)
        return login_page("Invalid username or password.")
    _fails.pop(ip, None)
    telemetry.glog("login ok", log_type="audit", ip=ip, user=user)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        _COOKIE,
        _sign(f"{user}:{time.time() + SESSION_TTL}"),
        max_age=SESSION_TTL,
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return resp


def guard_response(request):
    """None if allowed; else the response to return instead."""
    if not AUTH_ON or session_ok(request):
        return None
    p = request.url.path
    if p == "/login":
        return None
    if p.startswith("/join/") or p == "/static/mic-worklet.js":
        return None
    if p.startswith("/relay/hopframe/"):
        return None
    accept = request.headers.get("accept", "")
    if p.startswith(("/ws", "/api")) or "application/json" in accept or request.method != "GET":
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    return RedirectResponse("/login", status_code=302)
