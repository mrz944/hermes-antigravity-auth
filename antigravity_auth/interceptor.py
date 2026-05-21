"""HTTP interceptor — patches GeminiCloudCodeClient to transform requests
via a custom httpx.Client subclass that overrides send()."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .config import get_config
from .endpoints import select_endpoint
from .transform.envelope import (
    build_antigravity_headers,
    build_antigravity_envelope,
    resolve_model_for_header_style,
)

logger = logging.getLogger(__name__)

_PATCHED = False
_ORIGINAL_INIT = None


# =============================================================================
# Custom httpx Client — intercepts send() to transform requests
# =============================================================================

class _AntigravityClient(httpx.Client):
    """httpx.Client subclass that transforms Cloud Code requests to Antigravity format."""

    def send(self, request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        url_str = str(request.url)
        if "cloudcode-pa.googleapis.com" not in url_str:
            return super().send(request, *args, **kwargs)

        # Read the body
        body_bytes = request.read()
        try:
            body = json.loads(body_bytes)
        except (json.JSONDecodeError, TypeError):
            return super().send(request, *args, **kwargs)

        if not isinstance(body, dict) or "request" not in body:
            return super().send(request, *args, **kwargs)

        config = get_config()
        model = str(body.get("model", ""))
        project_id = str(body.get("project", ""))
        inner = body["request"]

        header_style = "gemini-cli" if config.cli_first else "antigravity"
        model = resolve_model_for_header_style(model, header_style)

        envelope = build_antigravity_envelope(
            request_payload=inner,
            model=model,
            project_id=project_id,
            header_style=header_style,
        )

        # --- Strip Claude thinking blocks ---
        if _is_claude_model(model) and not config.keep_thinking:
            inner_req = envelope.get("request") if isinstance(envelope, dict) else {}
            if isinstance(inner_req, dict) and "contents" in inner_req:
                from .transform.thinking import strip_all_thinking_blocks
                strip_all_thinking_blocks(inner_req["contents"])

        # --- Sanitize tool schemas ---
        if config.claude_tool_hardening:
            inner_req = envelope.get("request") if isinstance(envelope, dict) else {}
            tools = inner_req.get("tools") if isinstance(inner_req, dict) else None
            if isinstance(tools, list):
                from .transform.schema import clean_json_schema
                for tool in tools:
                    if isinstance(tool, dict):
                        func_decls = tool.get("functionDeclarations")
                        if isinstance(func_decls, list):
                            for fd in func_decls:
                                if isinstance(fd, dict) and "parameters" in fd:
                                    fd["parameters"] = clean_json_schema(fd["parameters"])
                        elif "parameters" in tool:
                            tool["parameters"] = clean_json_schema(tool["parameters"])

        # --- Rewrite URL ---
        endpoint = select_endpoint(config)
        new_url_str = url_str.replace("https://cloudcode-pa.googleapis.com", endpoint)
        new_url = httpx.URL(new_url_str) if new_url_str != url_str else request.url

        # --- Rewrite headers ---
        antigravity_headers = build_antigravity_headers(header_style=header_style)
        new_headers = httpx.Headers(request.headers)
        for key, val in antigravity_headers.items():
            new_headers[key] = val

        # --- Inject fingerprint ---
        try:
            from .fingerprint import generate_fingerprint
            fp = generate_fingerprint()
            if fp:
                ua = fp.get("userAgent")
                if ua and isinstance(ua, str):
                    new_headers["User-Agent"] = ua
                cm = fp.get("clientMetadata")
                if cm:
                    new_headers["Client-Metadata"] = json.dumps(cm)
        except Exception:
            pass

        # Build a fresh request with the transformed body
        new_request = httpx.Request(
            method=request.method,
            url=new_url,
            headers=new_headers,
            json=envelope,
        )
        new_request.read()  # pre-load body for non-streaming
        return super().send(new_request, *args, **kwargs)


def _is_claude_model(model: str) -> bool:
    try:
        from .transform.messages import is_claude_model
        return is_claude_model(model)
    except Exception:
        return "claude" in model.lower()


# =============================================================================
# Response hook
# =============================================================================

def _antigravity_response_hook(response: httpx.Response) -> None:
    """Handle side effects after Antigravity responses (401 refresh, 429 rotation)."""
    from .config import get_config
    config = get_config()

    if response.status_code == 401 and config.proactive_token_refresh:
        try:
            from .token import refresh_access_token
            from .storage import load_accounts
            from .cli import sync_token_to_google_oauth
            accounts_data = load_accounts()
            accs = accounts_data.get("accounts", [])
            active_idx = accounts_data.get("activeIndex", 0)
            if 0 <= active_idx < len(accs):
                acc = accs[active_idx]
                refresh = acc.get("refreshToken", "")
                if refresh:
                    r = refresh_access_token({"refresh": refresh})
                    if r.get("access"):
                        sync_token_to_google_oauth(
                            access_token=r["access"], refresh_token=refresh,
                            project_id=acc.get("projectId", ""), email=acc.get("email"),
                            expires_ms=r.get("expires"),
                        )
        except Exception as exc:
            logger.warning("Token refresh failed: %s", exc)

    if response.status_code >= 500:
        try:
            from .endpoints import mark_endpoint_failed
            from urllib.parse import urlparse
            p = urlparse(str(response.request.url))
            mark_endpoint_failed(f"https://{p.netloc}")
        except Exception:
            pass


# =============================================================================
# Install
# =============================================================================

def _wrap_http_client(http_client: httpx.Client) -> httpx.Client:
    """Replace the httpx.Client with our AntigravityClient subclass."""
    wrapped = _AntigravityClient(transport=http_client._transport)
    if not wrapped.event_hooks.get("response"):
        wrapped.event_hooks["response"] = []
    wrapped.event_hooks["response"].append(_antigravity_response_hook)
    return wrapped


def install() -> bool:
    global _PATCHED, _ORIGINAL_INIT
    if _PATCHED:
        return False
    try:
        from agent.gemini_cloudcode_adapter import GeminiCloudCodeClient
    except ImportError:
        logger.warning("GeminiCloudCodeClient not importable — is hermes-agent installed?")
        return False

    _ORIGINAL_INIT = GeminiCloudCodeClient.__init__

    def _patched_init(self, *args: Any, **kwargs: Any) -> None:
        _ORIGINAL_INIT(self, *args, **kwargs)
        self._http = _wrap_http_client(self._http)

    GeminiCloudCodeClient.__init__ = _patched_init
    _PATCHED = True
    logger.info("Antigravity HTTP interceptor installed (httpx.Client subclass)")
    return True


def is_installed() -> bool:
    return _PATCHED
