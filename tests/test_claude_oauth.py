import base64
import hashlib
import io
import json
import re
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

from ai_meter.collectors.claude_oauth import (
    OAuthError,
    OAuthLoginFlow,
    parse_authorization_code,
    write_credentials,
)

_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *_a: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


class PkceTests(unittest.TestCase):
    def test_verifier_and_challenge_are_unpadded_base64url(self) -> None:
        flow = OAuthLoginFlow()
        for value in (flow.verifier, flow.challenge, flow.state):
            self.assertTrue(_B64URL.match(value), value)
            self.assertNotIn("=", value)

    def test_challenge_is_sha256_of_verifier(self) -> None:
        flow = OAuthLoginFlow()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(flow.verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        self.assertEqual(flow.challenge, expected)

    def test_state_is_distinct_from_verifier(self) -> None:
        flow = OAuthLoginFlow()
        self.assertNotEqual(flow.state, flow.verifier)


class AuthorizeUrlTests(unittest.TestCase):
    def test_url_carries_pkce_and_manual_flow_params(self) -> None:
        flow = OAuthLoginFlow()
        parsed = urllib.parse.urlparse(flow.authorize_url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "claude.ai")
        q = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(q["code"], ["true"])
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertEqual(q["code_challenge"], [flow.challenge])
        self.assertEqual(q["state"], [flow.state])
        self.assertIn("user:inference", q["scope"][0])
        self.assertTrue(q["redirect_uri"][0].endswith("/oauth/code/callback"))


class ParseCodeTests(unittest.TestCase):
    def test_splits_on_hash(self) -> None:
        self.assertEqual(parse_authorization_code("abc123#state456"), "abc123")

    def test_handles_no_fragment_and_whitespace(self) -> None:
        self.assertEqual(parse_authorization_code("  abc123  "), "abc123")

    def test_empty(self) -> None:
        self.assertEqual(parse_authorization_code("   "), "")


class ExchangeTests(unittest.TestCase):
    def test_success_returns_token_and_sends_pkce(self) -> None:
        flow = OAuthLoginFlow()
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=None):  # noqa: ANN001
            captured["req"] = req
            return _FakeResp(
                json.dumps(
                    {
                        "access_token": "sk-ant-oat01-new",
                        "refresh_token": "sk-ant-ort01-new",
                        "expires_in": 28800,
                        "scope": "user:inference user:profile",
                    }
                ).encode()
            )

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            token = flow.exchange("the-code#the-state")

        self.assertEqual(token["access_token"], "sk-ant-oat01-new")
        body = urllib.parse.parse_qs(captured["req"].data.decode())
        self.assertEqual(body["grant_type"], ["authorization_code"])
        self.assertEqual(body["code"], ["the-code"])  # fragment stripped
        self.assertEqual(body["code_verifier"], [flow.verifier])
        self.assertEqual(body["state"], [flow.state])

    def test_http_error_surfaces_body(self) -> None:
        flow = OAuthLoginFlow()

        def fake_urlopen(req, timeout=None):  # noqa: ANN001
            raise urllib.error.HTTPError(
                url="https://x",
                code=400,
                msg="Bad Request",
                hdrs={},
                fp=io.BytesIO(b'{"error":"invalid_grant"}'),
            )

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(OAuthError) as ctx:
                flow.exchange("code#state")
        self.assertIn("400", str(ctx.exception))
        self.assertIn("invalid_grant", str(ctx.exception))

    def test_missing_access_token_raises(self) -> None:
        flow = OAuthLoginFlow()

        def fake_urlopen(req, timeout=None):  # noqa: ANN001
            return _FakeResp(json.dumps({"token_type": "Bearer"}).encode())

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(OAuthError):
                flow.exchange("code#state")

    def test_empty_code_raises_without_network(self) -> None:
        flow = OAuthLoginFlow()
        with self.assertRaises(OAuthError):
            flow.exchange("   ")


class WriteCredentialsTests(unittest.TestCase):
    def test_creates_file_in_claude_code_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".claude"
            path = write_credentials(
                home,
                {
                    "access_token": "Bearer sk-ant-oat01-x",
                    "refresh_token": "sk-ant-ort01-y",
                    "expires_in": 28800,
                    "scope": "user:inference user:profile",
                },
            )
            self.assertTrue(path.exists())
            oauth = json.loads(path.read_text(encoding="utf-8"))["claudeAiOauth"]
            # "Bearer " prefix stripped; stored bare like Claude Code does.
            self.assertEqual(oauth["accessToken"], "sk-ant-oat01-x")
            self.assertEqual(oauth["refreshToken"], "sk-ant-ort01-y")
            self.assertEqual(oauth["scopes"], ["user:inference", "user:profile"])
            self.assertGreater(oauth["expiresAt"], 0)

    def test_preserves_existing_unrelated_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".claude"
            home.mkdir(parents=True)
            path = home / ".credentials.json"
            path.write_text(
                json.dumps(
                    {
                        "otherKey": {"keep": "me"},
                        "claudeAiOauth": {
                            "accessToken": "old",
                            "subscriptionType": "max",
                        },
                    }
                ),
                encoding="utf-8",
            )
            write_credentials(
                home, {"access_token": "new", "expires_in": 100}
            )
            obj = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(obj["otherKey"], {"keep": "me"})
            self.assertEqual(obj["claudeAiOauth"]["accessToken"], "new")
            # subscriptionType is untouched by the token write.
            self.assertEqual(obj["claudeAiOauth"]["subscriptionType"], "max")


if __name__ == "__main__":
    unittest.main()
