# Remaining Features — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Wire the last P2/P3 features and eliminate remaining dead code to achieve feature parity with the TypeScript original.

**Architecture:** Four focused workstreams: quota API integration (user-visible), proactive token refresh (reliability), dead code cleanup (hygiene), and live smoke test (validation). Thinking signature caching is deferred — the interceptor runs at the HTTP layer without session context, making it a significant architectural challenge.

**Tech Stack:** Python 3.11+, urllib.request (quota API), threading.Timer (token refresh), pytest

---

## Task 1: Wire live quota checking

**Objective:** `check_quotas_and_verify()` currently just prints "OK (Active)" without calling any API. Wire it to actually hit Google's quota endpoint and display real usage data.

**Files:**
- Modify: `antigravity_auth/cli.py:330-349` (check_quotas_and_verify)
- Modify: `antigravity_auth/accounts/quota.py` (add fetch function)
- Test: `antigravity_auth/test_cli.py`

**Background:** The Antigravity API exposes `v1internal:retrieveUserQuota` which returns quota groups with `used`, `limit`, and `remaining` values. The TypeScript original calls this via the same envelope format used for chat completions.

**Step 1: Add `fetch_quota_from_api()` to quota.py**

Read `antigravity_auth/accounts/quota.py`. Add this function:

```python
def fetch_quota_from_api(access_token: str, project_id: str) -> dict[str, Any] | None:
    """Fetch live quota data from the Antigravity API.

    Returns parsed quota groups dict, or None on failure.
    """
    import json
    import urllib.request
    from ..constants import ANTIGRAVITY_ENDPOINT_PROD, get_antigravity_headers

    url = f"{ANTIGRAVITY_ENDPOINT_PROD}/v1internal:retrieveUserQuota"
    headers = get_antigravity_headers()
    headers["Authorization"] = f"Bearer {access_token}"
    headers["Content-Type"] = "application/json"

    body = json.dumps({"project": project_id}).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("quotaGroups") or data
    except Exception:
        return None
```

**Step 2: Replace the stub check_quotas_and_verify()**

In `cli.py`, replace the current stub (which just checks if refresh_token exists) with:

```python
def check_quotas_and_verify():
    accounts_data = load_accounts()
    accounts = accounts_data.get("accounts", [])
    if not accounts:
        print("No accounts registered.")
        return

    print("\nVerifying Account Status & Quotas:")
    print("=" * 60)
    for idx, acc in enumerate(accounts):
        email = acc.get("email", "Unknown")
        project_id = acc.get("projectId") or "<none>"
        
        refresh_token = acc.get("refreshToken", "")
        if not refresh_token:
            print(f"[{idx}] {email} -> FAILED (Missing credentials)")
            continue

        # Refresh access token
        try:
            from .token import refresh_access_token
            refreshed = refresh_access_token({"refresh": refresh_token})
            access_token = refreshed.get("access", "")
        except Exception:
            print(f"[{idx}] {email} -> FAILED (Token refresh error)")
            continue

        if not access_token:
            print(f"[{idx}] {email} -> FAILED (No access token)")
            continue

        # Fetch quota
        from .accounts.quota import fetch_quota_from_api
        quota = fetch_quota_from_api(access_token, project_id)
        
        if quota is None:
            print(f"[{idx}] {email} (Project: {project_id}) -> Token valid, quota fetch failed")
            continue

        # Display quota groups
        print(f"[{idx}] {email} (Project: {project_id})")
        for group_name, group_data in quota.items():
            if not isinstance(group_data, dict):
                continue
            used = group_data.get("used", "?")
            limit = group_data.get("limit", "?")
            remaining = group_data.get("remaining", "?")
            print(f"    {group_name}: {used}/{limit} used ({remaining} remaining)")
    print("=" * 60)
```

**Step 3: Verify**

```bash
python3 -c "from antigravity_auth.accounts.quota import fetch_quota_from_api; print('import OK')"
python3 -m pytest antigravity_auth/test_cli.py -v
python3 -m pytest antigravity_auth/ -q
```

**Step 4: Commit**

```bash
git add antigravity_auth/cli.py antigravity_auth/accounts/quota.py
git commit -m "feat: wire live quota checking via Antigravity API"
```

---

## Task 2: Proactive token refresh background thread

**Objective:** Tokens currently only refresh on 401. Add proactive refresh — check token expiry periodically and refresh before expiration using `proactive_refresh_buffer_seconds` (default 1800s = 30 min).

**Files:**
- Create: `antigravity_auth/token_watchdog.py`
- Modify: `antigravity_auth/hermes_plugin.py` (start watchdog on plugin load)

**Step 1: Create token_watchdog.py**

```python
"""Background token refresh watchdog.

Polls Hermes' OAuth token store periodically and refreshes
tokens before they expire (buffer: proactive_refresh_buffer_seconds).
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_watchdog_thread: threading.Thread | None = None
_watchdog_stop: threading.Event = threading.Event()


def _watchdog_loop() -> None:
    """Background loop: check token expiry, refresh if needed."""
    from .config import get_config

    while not _watchdog_stop.is_set():
        try:
            config = get_config()
            if not config.proactive_token_refresh:
                _watchdog_stop.wait(config.proactive_refresh_check_interval_seconds)
                continue

            _refresh_if_needed(config)
        except Exception as exc:
            logger.debug("Token watchdog error: %s", exc)

        _watchdog_stop.wait(config.proactive_refresh_check_interval_seconds)


def _refresh_if_needed(config) -> None:
    """Check the active account's token and refresh if within buffer."""
    try:
        from .storage import load_accounts
        from .token import refresh_access_token
        from agent.google_oauth import load_credentials as load_google_creds
    except ImportError:
        return  # Hermes not available

    creds = load_google_creds()
    if not creds or not creds.refresh_token:
        return

    now_ms = int(time.time() * 1000)
    expires_ms = getattr(creds, 'expires_ms', 0) or 0
    buffer_ms = config.proactive_refresh_buffer_seconds * 1000

    if expires_ms > 0 and (expires_ms - now_ms) > buffer_ms:
        return  # Token still fresh

    # Token is within buffer window — refresh
    try:
        from .cli import sync_token_to_google_oauth
        accounts_data = load_accounts()
        active_idx = accounts_data.get("activeIndex", 0)
        accounts = accounts_data.get("accounts", [])

        if 0 <= active_idx < len(accounts):
            acc = accounts[active_idx]
            refreshed = refresh_access_token({"refresh": acc.get("refreshToken", "")})
            if refreshed.get("access"):
                sync_token_to_google_oauth(
                    access_token=refreshed["access"],
                    refresh_token=acc.get("refreshToken", ""),
                    project_id=acc.get("projectId", ""),
                    email=acc.get("email"),
                    expires_ms=refreshed.get("expires"),
                )
                logger.info("Proactively refreshed token for %s", acc.get("email"))
    except Exception as exc:
        logger.debug("Proactive token refresh failed: %s", exc)


def start_watchdog() -> None:
    """Start the background token refresh thread. Idempotent."""
    global _watchdog_thread
    if _watchdog_thread is not None and _watchdog_thread.is_alive():
        return
    _watchdog_stop.clear()
    _watchdog_thread = threading.Thread(target=_watchdog_loop, daemon=True, name="antigravity-token-watchdog")
    _watchdog_thread.start()
    logger.info("Token watchdog started")


def stop_watchdog() -> None:
    """Stop the background token refresh thread."""
    _watchdog_stop.set()
    global _watchdog_thread
    if _watchdog_thread is not None:
        _watchdog_thread.join(timeout=5)
        _watchdog_thread = None
```

**Step 2: Wire watchdog into plugin load**

In `antigravity_auth/hermes_plugin.py`, after the tool registration block, add:

```python
    # Start background token refresh watchdog
    try:
        from .token_watchdog import start_watchdog
        start_watchdog()
    except Exception:
        pass
```

**Step 3: Verify**

```bash
python3 -c "from antigravity_auth.token_watchdog import start_watchdog, stop_watchdog; start_watchdog(); stop_watchdog(); print('OK')"
python3 -m pytest antigravity_auth/ -q
```

**Step 4: Commit**

```bash
git add antigravity_auth/token_watchdog.py antigravity_auth/hermes_plugin.py
git commit -m "feat: add proactive token refresh watchdog thread"
```

---

## Task 3: Dead code cleanup

**Objective:** Remove remaining dead imports and prune orphaned module references.

**Files:**
- Modify: `antigravity_auth/oauth.py:9`
- Modify: `antigravity_auth/verification.py` (prune exports used only by tests)
- Modify: `antigravity_auth/fingerprint.py` (remove dead internal functions)

**Step 1: Remove dead import in oauth.py**

Line 9 of `oauth.py` imports `ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET` at module level but `authorize_antigravity()` now uses `require_credentials()`. The module-level import is dead.

```python
# Remove this import at line 9:
    from .constants import ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
```

But check if any other function in oauth.py uses these constants. If the `try/except` fallback path at line 12 also uses them, keep the import in the except block only.

Actually, the `try/except` block at lines 8-13 is the standard dual-import pattern. The `token.py` has the same pattern. Only `authorize_antigravity()` uses `require_credentials()` — the other functions like `exchange_antigravity()` and `refresh_access_token()` use the constants directly. So the import IS needed for those other functions. **Skip this step** — the import is still used elsewhere.

**Step 2: No-op — verification.py is fine**

`verification.py` provides public functions (`probe_account_health`, etc.) that are correctly imported by tests. The module is small (under 100 lines) and serves as a public API surface. Keep it.

**Step 3: Prune unused fingerprint internals**

`fingerprint.py` has `generate_device_id()` and `generate_session_token()` which are only called internally by `generate_fingerprint()`. Keep them — they're implementation details of the public `generate_fingerprint()` which IS called by the interceptor.

The module is clean. **No changes needed.**

**Verdict:** No dead code to clean. The earlier reviews already caught everything. Skip this task.

---

## Task 4: Live smoke test with actual Hermes

**Objective:** Verify the plugin works end-to-end with a real Hermes instance and OAuth tokens.

**This is a manual task — document the test procedure.**

**Prerequisites:**
- Hermes Agent installed with valid Google OAuth credentials
- Plugin installed: `pip install -e . && hermes-antigravity-install`
- At least one account authenticated: `hermes antigravity login`

**Test 1: Gemini model (baseline)**

```bash
hermes -z "Say hello in one word" --provider ag --model gemini-3-flash-preview
```

Expected: Response like "Hello" or similar. This should work (did before the changes).

**Test 2: Claude model (the critical path)**

```bash
hermes -z "Say hello in one word" --provider ag --model claude-sonnet-4-6
```

Expected: Response. If this works, the interceptor is correctly transforming requests.

**Test 3: Quota check**

```bash
hermes antigravity check
```

Expected: Real quota numbers displayed instead of the old stub "OK (Active)".

**Test 4: Verify interceptor activation**

```bash
HERMES_ANTIGRAVITY_DEBUG=1 hermes -z "Hi" --provider ag --model gemini-3-flash-preview 2>&1 | grep -i "interceptor installed"
```

Expected: Should find "Antigravity HTTP interceptor installed" in debug logs.

**Commit:** None needed — documentation-only task. Add results to the implementation plan doc.

---

## Summary

| Task | What | Files | New Code |
|------|------|-------|----------|
| 1 | Live quota checking | cli.py, quota.py | ~50 lines |
| 2 | Proactive token refresh | token_watchdog.py (new), hermes_plugin.py | ~100 lines |
| 3 | Dead code cleanup | None (already clean) | — |
| 4 | Smoke test | Manual | — |

Two new features, no deletions. Thinking signature caching deferred — requires session context not available at the HTTP layer. The interceptor approach fundamentally can't capture signatures across turns without Hermes-internal hooks.
