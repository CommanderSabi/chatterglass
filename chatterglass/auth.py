# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""Discord OAuth2 authorisation with PKCE.

Why not the RPC AUTHORIZE command? It returns a code that only Discord's
StreamKit service will exchange, and StreamKit refuses codes issued for an app
whose registered redirect is not its own. Why not a client secret? It cannot
ship inside an open-source app, and holding one server-side is what Overlayed
does with their API.

PKCE needs neither: the app opens Discord's ordinary authorise page, Discord
redirects back to a loopback address with a code, and the code is exchanged
together with a one-time verifier. That token is then handed to the local RPC
socket with AUTHENTICATE.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Optional

CALLBACK_PORT = 8765
REDIRECT_URI = "http://localhost:%d/callback" % CALLBACK_PORT
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
SCOPES = ["rpc", "identify"]
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class AuthError(Exception):
    pass


# Bound to the authorisation currently in flight, so a callback that was not
# started by this process is refused (login-CSRF / stray local requests).
_expected_state: Optional[str] = None


def _host_allowed(host_header: Optional[str]) -> bool:
    if not host_header:
        return False
    allowed = {"localhost:%d" % CALLBACK_PORT, "127.0.0.1:%d" % CALLBACK_PORT,
               "[::1]:%d" % CALLBACK_PORT}
    return host_header.strip().lower() in allowed


def _authorize_query(client_id: str, challenge: str, state: str) -> str:
    return urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "prompt": "consent",
    })


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _new_verifier() -> str:
    return _b64(secrets.token_bytes(64))


def _challenge(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode("ascii")).digest())


class _CallbackHandler(BaseHTTPRequestHandler):
    result: Optional[Dict[str, str]] = None

    def do_GET(self):  # noqa: N802 - required name
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            # Anything else (favicon, scanner, typo) must not end the flow: the
            # server keeps serving until the real callback arrives.
            self.send_response(404)
            self.end_headers()
            return
        if not _host_allowed(self.headers.get("Host")):
            self.send_response(400)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        state = (params.get("state") or [""])[0]
        if not _expected_state or state != _expected_state:
            # Not our request: refuse it and leave the flow waiting.
            self.send_response(403)
            self.end_headers()
            return
        _CallbackHandler.result = {key: values[0] for key, values in params.items()}
        body = (b"<!doctype html><meta charset='utf-8'><title>Chatterglass</title>"
                b"<body style='font-family:sans-serif;background:#14151a;color:#e9eaf0;"
                b"padding:3rem;text-align:center'>"
                b"<h2>Chatterglass</h2><p>Authorised. You can close this tab and go "
                b"back to the app.</p>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _post_form(url: str, fields: Dict[str, str]) -> Dict[str, object]:
    body = urllib.parse.urlencode(fields).encode("ascii")
    request = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise AuthError("Discord said HTTP %s: %s" % (error.code, detail))
    except Exception as error:  # noqa: BLE001
        raise AuthError("request failed: %r" % (error,))


def authorise(client_id: str, timeout: float = 240.0) -> Dict[str, object]:
    """Run the whole browser flow. Returns Discord's token response."""
    global _expected_state
    verifier = _new_verifier()
    state = secrets.token_urlsafe(24)
    _expected_state = state
    _CallbackHandler.result = None

    try:
        server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
    except OSError as error:
        _expected_state = None
        raise AuthError("cannot listen on port %d: %r" % (CALLBACK_PORT, error))

    url = AUTHORIZE_URL + "?" + _authorize_query(client_id, _challenge(verifier), state)

    try:
        import webbrowser
        if not webbrowser.open(url):
            raise AuthError("could not open a browser; go to: %s" % url)
    except AuthError:
        server.server_close()
        _expected_state = None
        raise
    except Exception:
        server.server_close()
        _expected_state = None
        raise AuthError("could not open a browser; go to: %s" % url)

    # Keep serving until the real callback arrives. Serving exactly one request
    # meant a favicon or a port scanner could abort the whole login.
    server.timeout = 1.0
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline and _CallbackHandler.result is None:
            server.handle_request()
    finally:
        server.server_close()
        _expected_state = None

    result = _CallbackHandler.result or {}
    if "error" in result:
        raise AuthError("Discord refused: %s" % result.get("error"))
    code = result.get("code")
    if not code:
        raise AuthError("no authorisation code came back (did you finish in the browser?)")

    token = _post_form(TOKEN_URL, {
        "client_id": client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    })
    if not token.get("access_token"):
        raise AuthError("no access_token in response: %s" % json.dumps(token)[:300])
    return token


def refresh(client_id: str, refresh_token: str) -> Dict[str, object]:
    token = _post_form(TOKEN_URL, {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })
    if not token.get("access_token"):
        raise AuthError("refresh failed: %s" % json.dumps(token)[:300])
    return token
