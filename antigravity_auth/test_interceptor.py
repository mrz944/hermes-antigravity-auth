"""Tests for the HTTP interceptor — _AntigravityClient.send() approach."""

import json
import unittest
import io

import httpx


class _FakeTransport:
    """Simulates a transport that captures the final httpcore.Request."""

    def __init__(self):
        self.last_request = None

    def handle_request(self, request):
        self.last_request = request
        return httpx.Response(
            status_code=200,
            content=b'{"candidates":[{"content":{"role":"model","parts":[{"text":"Hello"}]},"finishReason":"STOP"}]}',
            request=request,
        )


class TestAntigravityClient(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # httpx 0.28: Request.content is read-only unless we make it writable
        pass

    def setUp(self):
        from antigravity_auth.interceptor import _AntigravityClient
        self.transport = _FakeTransport()
        self.client = _AntigravityClient(transport=self.transport)

    def _make_code_assist_body(self, model="gemini-3-flash-preview"):
        return {
            "project": "test-project",
            "model": model,
            "user_prompt_id": "abc123",
            "request": {
                "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
            },
        }

    def test_transforms_envelope(self):
        body = self._make_code_assist_body()
        response = self.client.post(
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            json=body,
        )
        self.assertEqual(response.status_code, 200)
        captured = json.loads(self.transport.last_request.content)
        self.assertIn("requestType", captured)
        self.assertEqual(captured["requestType"], "agent")

    def test_preserves_authorization(self):
        body = self._make_code_assist_body()
        response = self.client.post(
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            json=body,
            headers={"Authorization": "Bearer test-token"},
        )
        captured = self.transport.last_request
        auth = [v for k, v in captured.headers.raw if k.lower() == b"authorization"]
        self.assertTrue(len(auth) > 0)

    def test_ignores_non_cloudcode(self):
        response = self.client.get("https://example.com/api")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.transport.last_request is not None)

    def test_passthrough_non_envelope(self):
        response = self.client.post(
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            json={"messages": [{"role": "user"}]},
        )
        captured = json.loads(self.transport.last_request.content)
        self.assertIn("messages", captured)
        self.assertNotIn("requestType", captured)
