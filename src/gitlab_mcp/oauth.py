"""OAuth 2.1 + DCR + passphrase login for the MCP HTTP transport.

claude.ai's Custom Connector backend discovers OAuth metadata via
/.well-known/oauth-* and refuses to send raw bearer tokens from the UI,
so the simplest auth that actually works is the OAuth code flow. This
module implements the minimum surface (DCR, PKCE-only code flow, opaque
tokens) gated by a single shared passphrase the user sets via the
MCP_PASSPHRASE env var.

Tokens live in process memory; restarting the server invalidates them
and claude.ai will silently re-auth (popping the passphrase form again).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

CODE_TTL_SECONDS = 600
ACCESS_TTL_SECONDS = 3600
REFRESH_TTL_SECONDS = 30 * 86400


class OAuthState:
    def __init__(self, passphrase: str) -> None:
        if not passphrase:
            raise ValueError("OAuthState requires a non-empty passphrase")
        self._passphrase = passphrase.encode()
        self.clients: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}
        self.access_tokens: dict[str, dict] = {}
        self.refresh_tokens: dict[str, dict] = {}

    @staticmethod
    def _now() -> int:
        return int(time.time())

    def _gc(self) -> None:
        now = self._now()
        self.codes = {k: v for k, v in self.codes.items() if v["expiry"] > now}
        self.access_tokens = {
            k: v for k, v in self.access_tokens.items() if v["expiry"] > now
        }
        self.refresh_tokens = {
            k: v for k, v in self.refresh_tokens.items() if v["expiry"] > now
        }

    def register_client(self, redirect_uris: list[str]) -> dict:
        cid = "mcp-" + secrets.token_urlsafe(12)
        client = {
            "client_id": cid,
            "redirect_uris": list(redirect_uris),
            "issued_at": self._now(),
        }
        self.clients[cid] = client
        return client

    def check_passphrase(self, passphrase: str) -> bool:
        return hmac.compare_digest(self._passphrase, passphrase.encode())

    def issue_code(self, client_id: str, redirect_uri: str, code_challenge: str) -> str:
        code = secrets.token_urlsafe(24)
        self.codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "expiry": self._now() + CODE_TTL_SECONDS,
        }
        return code

    def exchange_code(
        self, code: str, client_id: str, redirect_uri: str, code_verifier: str
    ) -> dict | None:
        self._gc()
        entry = self.codes.pop(code, None)
        if not entry:
            return None
        if entry["client_id"] != client_id or entry["redirect_uri"] != redirect_uri:
            return None
        digest = hashlib.sha256(code_verifier.encode()).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if not hmac.compare_digest(expected, entry["code_challenge"]):
            return None
        return self._issue_tokens(client_id)

    def refresh(self, refresh_token: str, client_id: str) -> dict | None:
        self._gc()
        entry = self.refresh_tokens.pop(refresh_token, None)
        if not entry or entry["client_id"] != client_id:
            return None
        return self._issue_tokens(client_id)

    def _issue_tokens(self, client_id: str) -> dict:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        now = self._now()
        self.access_tokens[access] = {
            "client_id": client_id,
            "expiry": now + ACCESS_TTL_SECONDS,
        }
        self.refresh_tokens[refresh] = {
            "client_id": client_id,
            "expiry": now + REFRESH_TTL_SECONDS,
        }
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": ACCESS_TTL_SECONDS,
            "refresh_token": refresh,
        }

    def validate_access(self, token: str) -> bool:
        self._gc()
        return token in self.access_tokens


def _base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{proto}://{host}"


_FORM_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Sign in to GitLab MCP</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background:#0f1115; color:#d8dee9; display:flex; align-items:center;
         justify-content:center; min-height:100vh; margin:0; }}
  .card {{ background:#1c1f26; padding:2rem 2.25rem; border-radius:8px;
          box-shadow:0 8px 24px rgba(0,0,0,.4); width:320px; }}
  h1 {{ margin:0 0 .5rem; font-size:1.05rem; }}
  p.sub {{ color:#8b96a8; margin:0 0 1.25rem; font-size:.85rem; }}
  label {{ display:block; font-size:.78rem; color:#8b96a8; margin-bottom:.35rem; }}
  input[type=password] {{ width:100%; padding:.55rem .65rem; background:#0f1115;
                         border:1px solid #2d3340; border-radius:5px; color:#d8dee9;
                         font-size:.95rem; box-sizing:border-box; }}
  button {{ margin-top:1rem; width:100%; padding:.6rem; background:#5e81ac;
           border:0; border-radius:5px; color:white; font-size:.95rem; cursor:pointer; }}
  button:hover {{ background:#6f95c1; }}
  .err {{ background:#4d2730; color:#ffb4b4; padding:.5rem .75rem;
         border-radius:4px; font-size:.85rem; margin-bottom:1rem; }}
</style></head>
<body><div class="card">
  <h1>Sign in to GitLab MCP</h1>
  <p class="sub">Enter the passphrase configured on the server.</p>
  {error}
  <form method="POST" action="/authorize">
    {hidden}
    <label for="p">Passphrase</label>
    <input type="password" name="passphrase" id="p" autofocus autocomplete="off" />
    <button type="submit">Authorize</button>
  </form>
</div></body></html>
"""


def _esc(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_form(params: dict, error: str | None = None) -> str:
    hidden = "\n".join(
        f'<input type="hidden" name="{_esc(k)}" value="{_esc(v)}">'
        for k, v in params.items()
    )
    err_html = f'<div class="err">{_esc(error)}</div>' if error else ""
    return _FORM_HTML.format(hidden=hidden, error=err_html)


def build_oauth_app(state: OAuthState) -> Starlette:
    async def well_known_protected(request: Request):
        base = _base_url(request)
        return JSONResponse(
            {
                "resource": base,
                "authorization_servers": [base],
                "bearer_methods_supported": ["header"],
            }
        )

    async def well_known_authz(request: Request):
        base = _base_url(request)
        return JSONResponse(
            {
                "issuer": base,
                "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token",
                "registration_endpoint": f"{base}/register",
                "response_types_supported": ["code"],
                "grant_types_supported": [
                    "authorization_code",
                    "refresh_token",
                ],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
            }
        )

    async def register(request: Request):
        body = await request.json()
        redirect_uris = body.get("redirect_uris") or []
        if not redirect_uris:
            return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)
        client = state.register_client(redirect_uris)
        return JSONResponse(
            {
                "client_id": client["client_id"],
                "client_id_issued_at": client["issued_at"],
                "redirect_uris": client["redirect_uris"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
            status_code=201,
        )

    def _validate_auth_params(qp: dict) -> tuple[dict | None, str | None]:
        client_id = qp.get("client_id", "")
        redirect_uri = qp.get("redirect_uri", "")
        response_type = qp.get("response_type", "")
        code_challenge = qp.get("code_challenge", "")
        method = qp.get("code_challenge_method", "")
        if response_type != "code":
            return None, "Only response_type=code is supported"
        if not client_id or client_id not in state.clients:
            return None, "Unknown client_id"
        if redirect_uri not in state.clients[client_id]["redirect_uris"]:
            return None, "redirect_uri not registered"
        if method != "S256":
            return None, "PKCE S256 required"
        if not code_challenge:
            return None, "Missing code_challenge"
        return {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": method,
            "response_type": response_type,
            "state": qp.get("state", ""),
            "scope": qp.get("scope", ""),
        }, None

    async def authorize_get(request: Request):
        qp = dict(request.query_params)
        params, err = _validate_auth_params(qp)
        if err:
            return HTMLResponse(
                f"<h3>Authorization error</h3><p>{_esc(err)}</p>", status_code=400
            )
        return HTMLResponse(_render_form(params))

    async def authorize_post(request: Request):
        form = dict(await request.form())
        passphrase = form.pop("passphrase", "")
        params, err = _validate_auth_params(form)
        if err:
            return HTMLResponse(
                f"<h3>Authorization error</h3><p>{_esc(err)}</p>", status_code=400
            )
        if not state.check_passphrase(passphrase):
            return HTMLResponse(
                _render_form(params, error="Wrong passphrase. Try again."),
                status_code=401,
            )
        code = state.issue_code(
            params["client_id"], params["redirect_uri"], params["code_challenge"]
        )
        target = params["redirect_uri"] + "?" + urlencode(
            {"code": code, "state": params["state"]}
        )
        return RedirectResponse(target, status_code=302)

    async def token(request: Request):
        form = await request.form()
        grant = form.get("grant_type")
        client_id = form.get("client_id", "")
        if not client_id or client_id not in state.clients:
            return JSONResponse({"error": "invalid_client"}, status_code=400)
        if grant == "authorization_code":
            code = form.get("code")
            redirect_uri = form.get("redirect_uri")
            code_verifier = form.get("code_verifier")
            if not (code and redirect_uri and code_verifier):
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            tokens = state.exchange_code(code, client_id, redirect_uri, code_verifier)
            if not tokens:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(tokens)
        if grant == "refresh_token":
            rt = form.get("refresh_token")
            if not rt:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            tokens = state.refresh(rt, client_id)
            if not tokens:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(tokens)
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    return Starlette(
        routes=[
            Route(
                "/.well-known/oauth-protected-resource",
                well_known_protected,
                methods=["GET"],
            ),
            Route(
                "/.well-known/oauth-protected-resource/mcp",
                well_known_protected,
                methods=["GET"],
            ),
            Route(
                "/.well-known/oauth-authorization-server",
                well_known_authz,
                methods=["GET"],
            ),
            Route("/register", register, methods=["POST"]),
            Route("/authorize", authorize_get, methods=["GET"]),
            Route("/authorize", authorize_post, methods=["POST"]),
            Route("/token", token, methods=["POST"]),
        ]
    )


def oauth_bearer_asgi(app, state: OAuthState):
    """Wrap an ASGI app, requiring a valid Bearer access_token from `state`."""

    def _challenge(scope) -> bytes:
        headers = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode() or "localhost"
        proto = headers.get(b"x-forwarded-proto", b"https").decode()
        url = f"{proto}://{host}/.well-known/oauth-protected-resource"
        return f'Bearer resource_metadata="{url}"'.encode()

    async def middleware(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        token = ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token or not state.validate_access(token):
            body = b'{"error":"unauthorized"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", _challenge(scope)),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await app(scope, receive, send)

    return middleware


_OAUTH_PATHS = {"/register", "/authorize", "/token"}


def compose_app(mcp_app, oauth_app):
    """Path-dispatch ASGI app: OAuth endpoints to oauth_app, everything else to mcp_app."""

    async def dispatcher(scope, receive, send):
        if scope["type"] != "http":
            await mcp_app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith("/.well-known/") or path in _OAUTH_PATHS:
            await oauth_app(scope, receive, send)
            return
        await mcp_app(scope, receive, send)

    return dispatcher
