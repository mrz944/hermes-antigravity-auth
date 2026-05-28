import unittest


class TestRedaction(unittest.TestCase):
    def test_redact_secrets_removes_nested_tokens_but_keeps_metadata(self):
        from antigravity_auth.redaction import REDACTED, redact_secrets

        raw = {
            "Authorization": "Bearer raw-access-token",
            "tokens": {
                "access_token": "raw-access-token",
                "refreshToken": "raw-refresh-token",
                "accessTokenExpiresAt": 123,
            },
            "snapshot": {
                "access_token_cached": True,
                "access_token_expires_at": 456,
                "lastRefreshAt": 789,
            },
            "url": "https://example.test/callback?" + "code=" + "oauth-code-secret" + "&client_secret=" + "client-secret",
        }

        redacted = redact_secrets(raw)
        rendered = str(redacted)
        self.assertNotIn("raw-access-token", rendered)
        self.assertNotIn("raw-refresh-token", rendered)
        self.assertNotIn("oauth-code-secret", rendered)
        self.assertNotIn("client-secret", rendered)
        self.assertEqual(redacted["tokens"]["access_token"], REDACTED)
        self.assertEqual(redacted["tokens"]["refreshToken"], REDACTED)
        self.assertEqual(redacted["tokens"]["accessTokenExpiresAt"], 123)
        self.assertTrue(redacted["snapshot"]["access_token_cached"])
        self.assertEqual(redacted["snapshot"]["access_token_expires_at"], 456)
