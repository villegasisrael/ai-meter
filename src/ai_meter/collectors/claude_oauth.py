"""Interactive OAuth (PKCE) re-login for Claude, mirroring Claude Code's flow.

This lets ai-meter obtain a fresh OAuth token without depending on the user
opening Claude Code, by replaying the same Authorization Code + PKCE handshake:

    1. Open ``claude.ai/oauth/authorize`` in the browser (manual ``code=true``
       mode, so no local callback server is required).
    2. The user authorizes and copies the displayed ``<code>#<state>`` string.
    3. We exchange it at the token endpoint for an access/refresh token.
    4. We persist it to ``~/.claude/.credentials.json`` in Claude Code's own
       ``claudeAiOauth`` shape, so both ai-meter and Claude Code keep working.

No model tokens are consumed. The collector picks the new credentials up
automatically via its on-disk mtime watch (see ``ClaudeApiUsageCollector``).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# These mirror the values baked into the Claude Code CLI. ``console.anthropic.com``
# is the legacy alias of ``platform.claude.com``; if Anthropic moves the flow,
# this is the single place to update.
_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_SCOPES = "user:inference user:profile user:sessions:claude_code user:mcp_servers"
_TIMEOUT_S = 30
_CREDENTIALS_NAME = ".credentials.json"


class OAuthError(Exception):
    """Raised when the OAuth login/exchange fails for any reason."""


def _b64url(raw: bytes) -> str:
    """Base64url without padding, per RFC 7636."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def parse_authorization_code(raw: str) -> str:
    """Pull the authorization code out of a pasted ``<code>#<state>`` string."""
    code = (raw or "").strip()
    if "#" in code:
        code = code.split("#", 1)[0]
    return code.strip()


class OAuthLoginFlow:
    """One manual PKCE login attempt. Build the URL, then exchange the code."""

    def __init__(self) -> None:
        self.verifier = _b64url(os.urandom(32))
        self.challenge = _b64url(hashlib.sha256(self.verifier.encode("ascii")).digest())
        # CSRF state is a separate random value, not derived from the verifier.
        self.state = _b64url(os.urandom(32))

    @property
    def authorize_url(self) -> str:
        params = {
            "code": "true",
            "client_id": _OAUTH_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": _REDIRECT_URI,
            "scope": _SCOPES,
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
            "state": self.state,
        }
        return f"{_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def exchange(self, pasted_code: str) -> dict[str, Any]:
        """Exchange the pasted code for tokens. Raises OAuthError on failure."""
        code = parse_authorization_code(pasted_code)
        if not code:
            raise OAuthError("no authorization code provided")
        body = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _REDIRECT_URI,
                "client_id": _OAUTH_CLIENT_ID,
                "code_verifier": self.verifier,
                "state": self.state,
            }
        ).encode()
        req = urllib.request.Request(
            _TOKEN_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "claude-code/2.0.32",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "ignore").strip()[:200]
            except Exception:
                pass
            suffix = f": {detail}" if detail else ""
            raise OAuthError(f"HTTP {exc.code} {exc.reason}{suffix}") from exc
        except urllib.error.URLError as exc:
            raise OAuthError(f"network error: {exc.reason}") from exc
        except Exception as exc:  # JSON decode, etc.
            raise OAuthError(str(exc)[:200]) from exc

        if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
            raise OAuthError("token endpoint returned no access_token")
        return data


def write_credentials(claude_home: Path, token: dict[str, Any]) -> Path:
    """Persist exchanged tokens to ~/.claude/.credentials.json (claudeAiOauth).

    Existing keys (including unrelated ones and ``subscriptionType``) are
    preserved; only the token fields are overwritten. Returns the file path.
    """
    path = Path(claude_home) / _CREDENTIALS_NAME
    path.parent.mkdir(parents=True, exist_ok=True)

    obj: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            if isinstance(loaded, dict):
                obj = loaded
        except Exception:
            obj = {}

    oauth = obj.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        oauth = {}

    access = str(token.get("access_token", "")).strip()
    # Stored bare (without "Bearer "), matching Claude Code's own format.
    if access.lower().startswith("bearer "):
        access = access[7:].strip()
    oauth["accessToken"] = access

    refresh = token.get("refresh_token")
    if isinstance(refresh, str) and refresh.strip():
        oauth["refreshToken"] = refresh.strip()

    expires_in = _safe_float(token.get("expires_in"))
    if expires_in is not None:
        oauth["expiresAt"] = int((time.time() + expires_in) * 1000)

    scope = token.get("scope")
    if isinstance(scope, str) and scope.strip():
        oauth["scopes"] = scope.split()
    elif "scopes" not in oauth:
        oauth["scopes"] = _SCOPES.split()

    obj["claudeAiOauth"] = oauth
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _safe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
