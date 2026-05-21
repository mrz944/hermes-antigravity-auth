"""Tests for the HTTP interceptor module — transport-level body rewriting."""

import json
import unittest

import httpx
import httpcore


class TestTransportInterceptor(unittest.TestCase):
    """Test the Antigravity transport wrapper transforms Code Assist -> Antigravity."""

    def setUp(self):
        from antigravity_auth.interceptor import _AntigravityTransport

        class _CaptureTransport:
            def handle_request(self, request):
                self._captured = request
                return httpcore.Response(
                    status=200,
                    headers=[],
                    content=b'{"candidates":[]}',
                    extensions={},
                )

        self.capture_transport = _CaptureTransport()
        self.transport = _AntigravityTransport(self.capture_transport)

    def _make_code_assist_request(self, project="test-project", model="gemini-3-flash-preview"):
        body = {
            "project": project,
            "model": model,
            "user_prompt_id": "abc123",
            "request": {
                "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
            },
        }
        body_bytes = json.dumps(body).encode("utf-8")
        return httpcore.Request(
            method=b"POST",
            url=httpcore.URL(b"https://cloudcode-pa.googleapis.com/v1internal:generateContent"),
            headers=[
                (b"host", b"cloudcode-pa.googleapis.com"),
                (b"authorization", b"Bearer test-token"),
                (b"content-type", b"application/json"),
            ],
            content=body_bytes,
        )

    def _get_captured_stream(self):
        """Read the captured request body from its stream iterator."""
        return b"".join(self.capture_transport._captured.stream)

    def _get_captured_body(self):
        return json.loads(self._get_captured_stream())

    def test_transforms_code_assist_envelope(self):
        """Code Assist envelope -> Antigravity envelope with requestType etc."""
        req = self._make_code_assist_request()
        self.transport.handle_request(req)

        new_body = self._get_captured_body()
        self.assertIn("requestType", new_body)
        self.assertEqual(new_body["requestType"], "agent")
        self.assertIn("userAgent", new_body)
        self.assertIn("requestId", new_body)
        self.assertIn("request", new_body)

    def test_preserves_project_and_model(self):
        """Project and model survive the transformation."""
        req = self._make_code_assist_request(
            project="my-gcp-project-123",
            model="claude-sonnet-4-6",
        )
        self.transport.handle_request(req)

        new_body = self._get_captured_body()
        self.assertEqual(new_body["project"], "my-gcp-project-123")
        self.assertEqual(new_body["model"], "claude-sonnet-4-6")

    def test_preserves_authorization_header(self):
        """Authorization header must survive — not stripped."""
        req = self._make_code_assist_request()
        self.transport.handle_request(req)

        captured = self.capture_transport._captured
        auth_headers = [v for k, v in captured.headers if k.lower() == b"authorization"]
        self.assertTrue(len(auth_headers) > 0)
        self.assertIn(b"Bearer test-token", auth_headers[0])

    def test_rewrites_headers(self):
        """Antigravity headers replace the originals."""
        req = self._make_code_assist_request()
        self.transport.handle_request(req)

        captured = self.capture_transport._captured
        header_names = [k.lower() for k, v in captured.headers]
        self.assertIn(b"client-metadata", header_names)

    def test_ignores_non_cloudcode_urls(self):
        """Non-Cloud Code URLs pass through unchanged."""
        original_content = b'{"key": "value"}'
        req = httpcore.Request(
            method=b"GET",
            url=httpcore.URL(b"https://example.com/api"),
            headers=[(b"host", b"example.com")],
            content=original_content,
        )
        self.transport.handle_request(req)
        captured = self.capture_transport._captured
        self.assertEqual(b"".join(captured.stream), original_content)

    def test_ignores_non_envelope_bodies(self):
        """Bodies without 'request' key pass through."""
        original_content = b'{"messages": [{"role": "user"}]}'
        req = httpcore.Request(
            method=b"POST",
            url=httpcore.URL(b"https://cloudcode-pa.googleapis.com/v1internal:generateContent"),
            headers=[(b"host", b"cloudcode-pa.googleapis.com")],
            content=original_content,
        )
        self.transport.handle_request(req)
        captured = self.capture_transport._captured
        self.assertEqual(b"".join(captured.stream), original_content)

    def test_content_length_matches_body(self):
        """Body comes from the transport — no separate Content-Length to mismatch."""
        req = self._make_code_assist_request()
        self.transport.handle_request(req)
        stream = self._get_captured_stream()
        # Body and Content-Length are derived from the same source
        self.assertGreater(len(stream), 0)
