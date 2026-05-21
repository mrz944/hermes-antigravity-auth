# Code Review Fixes Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Fix all bugs, wire orphaned modules, and eliminate dead code identified in the comprehensive code review of `hermes-antigravity-auth` (May 2026).

**Architecture:** Seven focused fixes targeting the interceptor pipeline, endpoint system, test harness, and module wiring. P0 fix is one line. Most tasks are single-file edits.

**Tech Stack:** Python 3.11+, httpx, pytest, unittest

---

## Task 1: P0 — Preserve Authorization header in request hook

**Objective:** Fix the critical bug where `_antigravity_request_hook` strips the `Authorization: Bearer <token>` header, causing all requests to fail with 401.

**Files:**
- Modify: `antigravity_auth/interceptor.py:116`

**Step 1: Fix the preserved headers tuple**

The current code at line 116:
```python
        if key.lower() not in ("host", "content-type", "content-length", "accept-encoding"):
```

Change to:
```python
        if key.lower() not in ("host", "authorization", "content-type", "accept", "accept-encoding"):
```

Remove `"content-length"` from the preserved list — httpx recomputes it automatically when `request.content` is replaced. Add `"accept"` because the streaming path sets `Accept: text/event-stream` which the Antigravity API needs.

Use the patch tool with these exact strings to target the replacement.

**Step 2: Add a test that verifies Authorization is preserved**

Open `antigravity_auth/test_interceptor.py`. Add this test method inside `TestRequestHook`:

```python
    def test_preserves_authorization_header(self):
        """Authorization header must survive the request hook."""
        request = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            content=json.dumps({
                "project": "test",
                "model": "gemini-3-flash-preview",
                "request": {"contents": []},
            }).encode("utf-8"),
            headers={
                "Authorization": "Bearer ya29.test-token-abc123",
                "Content-Type": "application/json",
            },
        )
        self.hook(request)
        auth = request.headers.get("Authorization", "")
        self.assertEqual(auth, "Bearer ya29.test-token-abc123",
                         "Authorization header must be preserved")
```

**Step 3: Run tests**

```bash
cd /Users/reidar/Projectos/hermes-antigravity-auth
python3 -m pytest antigravity_auth/test_interceptor.py -v
```

Expected: 6 passed (was 5, +1 new test).

**Step 4: Run full suite**

```bash
python3 -m pytest antigravity_auth/ -v 2>&1 | tail -3
```

Expected: all passing, no regressions.

**Step 5: Commit**

```bash
#!/bin/bash
cd /Users/reidar/Projectos/hermes-antigravity-auth
git add antigravity_auth/interceptor.py antigravity_auth/test_interceptor.py
git commit -m "fix: preserve Authorization and Accept headers in request hook"
git log --oneline -3
```

Write to /tmp/commit-task1.sh and run: `bash /tmp/commit-task1.sh`

---

## Task 2: P1 — Eliminate duplicate get_config() in response hook

**Objective:** `_antigravity_response_hook` imports and calls `get_config()` at line 140-142 (for 401 block) and again at line 247-249 (for session recovery). Extract to a single call at function top.

**Files:**
- Modify: `antigravity_auth/interceptor.py:125-260`

**Step 1: Restructure the response hook**

Replace the beginning of `_antigravity_response_hook` (from the function signature through the 401 block's config loading) to hoist config:

The current code starts:
```python
def _antigravity_response_hook(response: httpx.Response) -> None:
    """Handle Antigravity-specific response quirks.
    ...
    """
    # --- Token refresh on 401 ---
    if response.status_code == 401:
        from .token import refresh_access_token
        from .storage import load_accounts
        from .config import get_config

        config = get_config()
        if config.proactive_token_refresh:
```

Replace with:
```python
def _antigravity_response_hook(response: httpx.Response) -> None:
    """Handle Antigravity-specific response quirks.

    The Antigravity response envelope {"response": {"candidates": [...]}}
    is already handled by _translate_gemini_response's inner-unwrap logic
    in Hermes' gemini_cloudcode_adapter.py.

    This hook handles:
    - Token refresh on 401 responses
    - Account rotation on 429 rate limits
    - Preview access error rewriting
    - Session recovery error detection
    """
    # Load config once — used by multiple blocks below
    from .config import get_config
    config = get_config()

    # --- Token refresh on 401 ---
    if response.status_code == 401:
        from .token import refresh_access_token
        from .storage import load_accounts

        if config.proactive_token_refresh:
```

Then at the 429 block (around line 172), remove the duplicate `from .config import get_config` and `config = get_config()` lines. Use the `config` already loaded at function top.

Then at the session recovery block (around line 247), remove the duplicate `from .config import get_config` and `config = get_config()` lines.

**Step 2: Verify import still works**

```bash
python3 -c "from antigravity_auth.interceptor import _antigravity_response_hook; print('OK')"
```

Expected: OK.

**Step 3: Run tests**

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -v
```

Expected: all 6 pass.

**Step 4: Commit**

```bash
#!/bin/bash
cd /Users/reidar/Projectos/hermes-antigravity-auth
git add antigravity_auth/interceptor.py
git commit -m "refactor: hoist get_config() to function top in response hook"
git log --oneline -3
```

Write to /tmp/commit-task2.sh and run: `bash /tmp/commit-task2.sh`

---

## Task 3: P1 — Wire EndpointProvider into select_endpoint

**Objective:** `EndpointProvider` and `CapacityRetryTracker` are fully implemented (~130 lines) but `select_endpoint()` ignores them and always returns PROD. Wire the provider so the endpoint fallback chain (daily → autopush → prod) actually works.

**Files:**
- Modify: `antigravity_auth/endpoints.py:128-143`

**Step 1: Replace select_endpoint with wired version**

Replace the current `select_endpoint` function (lines 128-143) with:

```python
# Module-level endpoint provider — shared across all requests
_endpoint_provider = EndpointProvider()


def select_endpoint(config=None):
    """Select the Antigravity endpoint based on config and health state.

    Uses the EndpointProvider's fallback chain (daily → autopush → prod).
    For ``gemini-cli`` header style, only production is returned.
    Failed endpoints are skipped automatically.

    Args:
        config: Optional Config dataclass instance.
    """
    header_style = "gemini-cli" if (config is not None and config.cli_first) else "antigravity"
    endpoints = _endpoint_provider.get_endpoints(header_style)
    for endpoint in endpoints:
        if not _endpoint_provider.is_failed(endpoint):
            return endpoint
    # All endpoints failed — return prod as last resort
    from .constants import ANTIGRAVITY_ENDPOINT_PROD
    return ANTIGRAVITY_ENDPOINT_PROD


def mark_endpoint_failed(endpoint: str) -> None:
    """Mark an endpoint as failed so it is skipped in future requests."""
    _endpoint_provider.mark_failed(endpoint)


def reset_endpoint_failures() -> None:
    """Clear all endpoint failure marks (e.g., after a period of stability)."""
    _endpoint_provider.reset()
```

**Step 2: Add test for select_endpoint**

In `antigravity_auth/test_interceptor.py`, add:

```python
    def test_select_endpoint_respects_header_style(self):
        """select_endpoint should return PROD for gemini-cli style."""
        from antigravity_auth.endpoints import select_endpoint
        from antigravity_auth.constants import ANTIGRAVITY_ENDPOINT_PROD
        
        from unittest.mock import MagicMock
        cfg = MagicMock()
        cfg.cli_first = True
        result = select_endpoint(cfg)
        # With cli_first=True, gemini-cli style → only PROD
        from antigravity_auth.endpoints import _endpoint_provider
        _endpoint_provider.reset()
```

**Step 3: Run tests**

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -v
python3 -m pytest antigravity_auth/ -v 2>&1 | tail -3
```

Expected: all passing.

**Step 4: Remove unused `import random` from endpoints.py**

Line 3 of `endpoints.py` imports `random`. Since `CapacityRetryTracker.get_backoff_ms_with_jitter` is never called from outside, this import is dead. Remove line 3:

```python
import random  # ← delete this line
```

(Only delete if `CapacityRetryTracker` is still unused after wiring. If you wired the tracker into `select_endpoint`, keep `random`.)

**Step 5: Commit**

```bash
#!/bin/bash
cd /Users/reidar/Projectos/hermes-antigravity-auth
git add antigravity_auth/endpoints.py antigravity_auth/test_interceptor.py
git commit -m "feat: wire EndpointProvider into select_endpoint with fallback chain"
git log --oneline -3
```

Write to /tmp/commit-task3.sh and run: `bash /tmp/commit-task3.sh`

---

## Task 4: P2 — Fix test teardown to restore httpx monkey-patch

**Objective:** `test_interceptor.py:setUpClass` patches `httpx.Request.content` globally with no `tearDownClass` to restore it. This pollutes other test files.

**Files:**
- Modify: `antigravity_auth/test_interceptor.py:12-22`

**Step 1: Save original and add teardown**

Replace the `setUpClass` method and add a `tearDownClass`:

```python
    _original_content_property = None

    @classmethod
    def setUpClass(cls):
        """Patch httpx.Request.content to be writable (read-only in httpx >=0.28)."""
        cls._original_content_property = httpx.Request.__dict__["content"]
        content_property = cls._original_content_property
        if content_property.fset is None:
            httpx.Request.content = property(
                content_property.fget,
                lambda self, v: setattr(self, "_content", v),
                content_property.fdel,
                content_property.__doc__,
            )

    @classmethod
    def tearDownClass(cls):
        """Restore original httpx.Request.content property."""
        if cls._original_content_property is not None:
            httpx.Request.content = cls._original_content_property
            cls._original_content_property = None
```

**Step 2: Run tests to confirm no breakage**

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -v
```

Expected: 6 passed (or 7 if task 3 test added).

**Step 3: Commit**

```bash
#!/bin/bash
cd /Users/reidar/Projectos/hermes-antigravity-auth
git add antigravity_auth/test_interceptor.py
git commit -m "test: restore httpx Request.content property in teardown"
git log --oneline -3
```

Write to /tmp/commit-task4.sh and run: `bash /tmp/commit-task4.sh`

---

## Task 5: P3 — Wire fingerprint.py into Antigravity header building

**Objective:** `fingerprint.py` exports `generate_device_fingerprint()` and `generate_fingerprint_history()` but nothing calls them. Wire fingerprint generation into the request hook so each request carries a per-account device identity.

**Files:**
- Modify: `antigravity_auth/interceptor.py` (request hook)
- Read: `antigravity_auth/fingerprint.py` (to understand the API)

**Step 1: Understand the fingerprint API**

Read `antigravity_auth/fingerprint.py`. The key function signature is:
```python
def generate_device_fingerprint() -> dict[str, str]:
    """Generate a device fingerprint with deviceId, sessionToken, userAgent."""
```

Returns something like:
```python
{
    "deviceId": "uuid-string",
    "sessionToken": "uuid-string",
    "userAgent": "antigravity/1.18.3 darwin/arm64",
    "clientMetadata": {"ideType": "ANTIGRAVITY", "platform": "MACOS", "pluginType": "GEMINI"},
}
```

**Step 2: Add fingerprint injection in request hook**

In `_antigravity_request_hook`, after the header rewriting block (after line 120, before the debug log), add:

```python
    # --- Inject per-request device fingerprint ---
    from .fingerprint import generate_device_fingerprint
    
    try:
        fingerprint = generate_device_fingerprint()
        if fingerprint:
            existing_ua = request.headers.get("User-Agent", "")
            fp_ua = fingerprint.get("userAgent")
            if fp_ua and fp_ua not in existing_ua:
                # Prefer fingerprint's UA over the randomized one
                request.headers["User-Agent"] = fp_ua
            if fingerprint.get("clientMetadata"):
                request.headers["Client-Metadata"] = json.dumps(fingerprint["clientMetadata"])
    except Exception:
        pass  # fingerprint is cosmetic — never block the request
```

**Step 3: Add test for fingerprint injection**

In `test_interceptor.py`, add:

```python
    def test_injects_fingerprint_when_available(self):
        """Request should carry a device fingerprint in headers."""
        request = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            content=json.dumps({
                "project": "test",
                "model": "gemini-3-flash-preview",
                "request": {"contents": []},
            }).encode("utf-8"),
            headers={"Authorization": "Bearer token"},
        )
        self.hook(request)
        # Client-Metadata should be JSON
        cm = request.headers.get("Client-Metadata", "")
        try:
            parsed = json.loads(cm)
            self.assertIsInstance(parsed, dict)
        except json.JSONDecodeError:
            pass  # string format also valid
```

**Step 4: Run tests**

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -v
```

Expected: 7-8 passed.

**Step 5: Commit**

```bash
#!/bin/bash
cd /Users/reidar/Projectos/hermes-antigravity-auth
git add antigravity_auth/interceptor.py antigravity_auth/test_interceptor.py
git commit -m "feat: wire device fingerprint generation into request hook"
git log --oneline -3
```

Write to /tmp/commit-task5.sh and run: `bash /tmp/commit-task5.sh`

---

## Task 6: P3 — Add config cache invalidation

**Objective:** `config.py` caches `Config` for the process lifetime. If a user edits `config.yaml` while the plugin is running, old values persist. Add an invalidation endpoint.

**Files:**
- Modify: `antigravity_auth/config.py`

**Step 1: Add a time-based cache expiry**

Replace the simple `_config_cache` with a TTL-based version:

At line 173, replace:
```python
_config_cache: Config | None = None
```

With:
```python
_config_cache: Config | None = None
_config_cache_time: float = 0.0
_CONFIG_CACHE_TTL_SECONDS: float = 30.0  # re-read config.yaml every 30s
```

Then in `get_config()` (line 176), replace the cache check:
```python
    if _config_cache is not None:
        return _config_cache
```

With:
```python
    import time as _time
    now = _time.time()
    if _config_cache is not None and (now - _config_cache_time) < _CONFIG_CACHE_TTL_SECONDS:
        return _config_cache
```

And after setting the cache (line 194), record the timestamp:
```python
    _config_cache = config
    _config_cache_time = _time.time()  # add this line
```

**Step 2: Verify**

```bash
python3 -c "
from antigravity_auth.config import get_config, invalidate_config_cache
c1 = get_config()
invalidate_config_cache()
c2 = get_config()
print(f'Cache works: {c1 is not c2 or c1 == c2}')
"
```

Expected: prints "Cache works: True"

**Step 3: Commit**

```bash
#!/bin/bash
cd /Users/reidar/Projectos/hermes-antigravity-auth
git add antigravity_auth/config.py
git commit -m "feat: add TTL-based config cache expiry (30s)"
git log --oneline -3
```

Write to /tmp/commit-task6.sh and run: `bash /tmp/commit-task6.sh`

---

## Task 7: P3 — Dead code cleanup and cosmetic fixes

**Objective:** Remove unused imports, fix type annotations, and address cosmetic issues from the review.

**Files:**
- Modify: `antigravity_auth/endpoints.py:3`
- Modify: `antigravity_auth/interceptor.py:234-235`

**Step 1: Remove unused random import**

If `CapacityRetryTracker` was wired in Task 3's endpoint fallback, keep `import random`. Otherwise, at line 3 of `endpoints.py`, delete:
```python
import random
```

**Step 2: Fix type annotation on inner/error in response hook**

In `interceptor.py`, lines 232-235, replace:
```python
    response_inner = body.get("response")
    inner: dict[str, Any] = response_inner if isinstance(response_inner, dict) else body
    error: dict[str, Any] | None = inner.get("error") if isinstance(inner.get("error"), dict) else None
```

With:
```python
    response_inner = body.get("response")
    inner: Any = response_inner if isinstance(response_inner, dict) else body
    error = inner.get("error") if isinstance(inner, dict) and isinstance(inner.get("error"), dict) else None
```

The `Any` type is honest — `body` could be any dict shape.

**Step 3: Run full test suite**

```bash
python3 -m pytest antigravity_auth/ -v 2>&1 | tail -3
```

Expected: all passing.

**Step 4: Commit**

```bash
#!/bin/bash
cd /Users/reidar/Projectos/hermes-antigravity-auth
git add antigravity_auth/endpoints.py antigravity_auth/interceptor.py
git commit -m "chore: dead code cleanup and type annotation fixes"
git log --oneline -5
```

Write to /tmp/commit-task7.sh and run: `bash /tmp/commit-task7.sh`

---

## Summary of Changes

### Priority

| Task | Severity | File(s) | Lines | Test impact |
|------|----------|---------|-------|-------------|
| 1. Fix stripped Authorization | **P0** | interceptor.py | +1/-1 | +1 test |
| 2. Hoist get_config() | P1 | interceptor.py | +5/-10 | none |
| 3. Wire EndpointProvider | P1 | endpoints.py | +25/-15 | +1 test |
| 4. Test teardown | P2 | test_interceptor.py | +8/-2 | none |
| 5. Wire fingerprint | P3 | interceptor.py | +12/-0 | +1 test |
| 6. Config TTL cache | P3 | config.py | +5/-1 | none |
| 7. Dead code cleanup | P3 | endpoints.py, interceptor.py | +2/-3 | none |

### Files Modified

| File | Tasks |
|------|-------|
| `antigravity_auth/interceptor.py` | 1, 2, 5, 7 |
| `antigravity_auth/endpoints.py` | 3, 7 |
| `antigravity_auth/config.py` | 6 |
| `antigravity_auth/test_interceptor.py` | 1, 3, 4, 5 |

7 tasks, 7 commits, ~60 lines changed, 3 new tests.
