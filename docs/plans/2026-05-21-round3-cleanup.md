# Round 3 Cleanup — Implementation Plan

> **For Hermes:** These are all single-file, 1-3 line fixes. Execute directly, no subagents needed.

**Goal:** Fix the 8 issues from the round 3 code review — one critical bug (NameError landmine), four import/duplication issues, one wiring gap, and two cosmetic cleanups.

**Architecture:** All changes in two files: `antigravity_auth/endpoints.py` and `antigravity_auth/interceptor.py`.

**Tech Stack:** Python 3.11+

---

## Task 1: Critical — Restore `import random` in endpoints.py

**Objective:** `CapacityRetryTracker.get_backoff_ms_with_jitter` calls `random.random()` but `import random` was removed. This will NameError if the method is ever called. Either restore the import or delete the dead class. **Decision: delete the dead class.**

**Files:**
- Modify: `antigravity_auth/endpoints.py`

**Rationale:** `CapacityRetryTracker` (~80 lines) is never instantiated anywhere. Its only consumer would be `select_endpoint` which already handles fallback via `EndpointProvider`. YAGNI — delete it.

**Step 1: Delete the class**

Remove everything from line 46 (`class CapacityRetryTracker:`) through line 124 (the last `@property` before the blank line at 126). This deletes the class and all its methods.

Use the patch tool:
- `old_string`: `class CapacityRetryTracker:\n  """Tracks per-endpoint capacity retry counts...` through `  @property\n  def max_backoff_ms(self) -> int:\n    return self._max_backoff_ms`
- `new_string`: `""` (empty)

**Step 2: Verify**

```bash
python3 -c "from antigravity_auth.endpoints import select_endpoint, EndpointProvider; print('OK')"
python3 -m pytest antigravity_auth/ -q
```

**Step 3: Commit**

```bash
git add antigravity_auth/endpoints.py
git commit -m "chore: remove unused CapacityRetryTracker class"
```

---

## Task 2: Remove duplicate `get_config()` in 429 block

**Objective:** `_antigravity_response_hook` loads config at function top (line 151) but the 429 block re-loads it (lines 193-195). Remove the duplicate.

**Files:**
- Modify: `antigravity_auth/interceptor.py:193-195`

**Step 1: Delete the duplicate lines**

Find:
```python
        from .accounts.state import ModelFamily, HeaderStyle
        from .config import get_config

        config = get_config()
```

Replace with:
```python
        from .accounts.state import ModelFamily, HeaderStyle
```

(The `config` variable from line 152 is already in scope.)

**Step 2: Verify**

```bash
python3 -c "from antigravity_auth.interceptor import _antigravity_response_hook; print('OK')"
python3 -m pytest antigravity_auth/ -q
```

**Step 3: Commit**

```bash
git add antigravity_auth/interceptor.py
git commit -m "refactor: remove duplicate get_config() in 429 block"
```

---

## Task 3: Remove dead imports

**Objective:** Three dead/redundant imports across two files.

**Files:**
- Modify: `antigravity_auth/interceptor.py:67-69`
- Modify: `antigravity_auth/endpoints.py:141`

**Step 1: Remove dead import in request hook**

In `_antigravity_request_hook`, remove lines 66-69:
```python
    from .constants import (
        ANTIGRAVITY_ENDPOINT_PROD,
    )
```

The endpoint selection is handled by `select_endpoint()` which imports its own constant.

**Step 2: Remove redundant re-import in select_endpoint**

In `endpoints.py:141`, remove:
```python
    from .constants import ANTIGRAVITY_ENDPOINT_PROD
```

This is already imported at module level on line 5.

**Step 3: Verify**

```bash
python3 -m pytest antigravity_auth/ -q
```

**Step 4: Commit**

```bash
git add antigravity_auth/interceptor.py antigravity_auth/endpoints.py
git commit -m "chore: remove dead and redundant imports"
```

---

## Task 4: Wire `mark_endpoint_failed` into response hook

**Objective:** `mark_endpoint_failed()` and `reset_endpoint_failures()` are exported but never called. The endpoint fallback chain never actually falls back because nothing marks endpoints as failed. Call `mark_endpoint_failed` on 5xx/connection errors so the next request skips to the next endpoint.

**Files:**
- Modify: `antigravity_auth/interceptor.py`

**Step 1: Add endpoint failure marking**

In `_antigravity_response_hook`, right after `if not response.is_success: return` (line 237-238), add before the `return`:

```python
    if not response.is_success:
        # Mark endpoint as failed on server errors so fallback chain activates
        if response.status_code >= 500 or response.status_code == 0:
            try:
                from .endpoints import mark_endpoint_failed
                from urllib.parse import urlparse
                endpoint = urlparse(str(response.url)).netloc or urlparse(str(response.url)).hostname or ""
                if endpoint:
                    mark_endpoint_failed(f"https://{endpoint}")
            except Exception:
                pass
        return
```

Wait — the response hook doesn't have access to the original URL that was targeted. The `response.url` may be the rewritten one. Actually, `response.request` is available on httpx.Response. Let me check...

Actually in httpx, `response.request` gives the original request. So:

```python
    if not response.is_success:
        # Mark endpoint as failed on server errors so fallback chain activates
        if response.status_code >= 500:
            try:
                from .endpoints import mark_endpoint_failed
                req_url = str(response.request.url)
                from urllib.parse import urlparse
                parsed = urlparse(req_url)
                endpoint = f"https://{parsed.netloc}"
                mark_endpoint_failed(endpoint)
            except Exception:
                pass
        return
```

**Step 2: Verify**

```bash
python3 -m pytest antigravity_auth/ -q
```

**Step 3: Commit**

```bash
git add antigravity_auth/interceptor.py
git commit -m "feat: mark endpoints as failed on server errors for fallback chain"
```

---

## Task 5: Fix fragile `is_claude_model` import

**Objective:** `interceptor.py:85` imports `is_claude_model` from `.transform` which re-exports from `transform.messages`. Import directly from the source module.

**Files:**
- Modify: `antigravity_auth/interceptor.py:85`

**Step 1: Fix the import**

Change line 85 from:
```python
    from .transform import is_claude_model, strip_all_thinking_blocks
```

To:
```python
    from .transform.thinking import strip_all_thinking_blocks
    from .transform.messages import is_claude_model
```

**Step 2: Verify**

```bash
python3 -m pytest antigravity_auth/ -q
```

**Step 3: Commit**

```bash
git add antigravity_auth/interceptor.py
git commit -m "refactor: import is_claude_model from source module"
```

---

## Task 6: Move safe imports to module level

**Objective:** Several imports in `_antigravity_request_hook` don't need deferred loading — they import from within the same package. Move them to the module level.

**Files:**
- Modify: `antigravity_auth/interceptor.py:6-10` (add to existing imports) and remove from lines 30-35, 66-70

**Step 1: Add stable imports at module level**

Change lines 6-10 from:
```python
import json
import logging
from typing import Any

import httpx
```

To:
```python
import json
import logging
from typing import Any

import httpx

from .config import get_config
from .transform.envelope import (
    build_antigravity_headers,
    build_antigravity_envelope,
    resolve_model_for_header_style,
)
from .endpoints import select_endpoint
```

**Step 2: Remove deferred imports from function body**

In `_antigravity_request_hook`, remove lines 30-35:
```python
    from .transform.envelope import (
        build_antigravity_headers,
        build_antigravity_envelope,
        resolve_model_for_header_style,
    )
    from .config import get_config
```

And remove lines 66-70:
```python
    from .constants import (
        ANTIGRAVITY_ENDPOINT_PROD,
    )
    from .endpoints import select_endpoint
```

(These were already removed in Tasks 3 but double-check.)

**Step 3: Verify**

```bash
python3 -m pytest antigravity_auth/ -q
```

**Step 4: Commit**

```bash
git add antigravity_auth/interceptor.py
git commit -m "refactor: move safe imports to module level"
```

---

## Summary

| Task | File(s) | Lines | Commit message |
|------|---------|-------|----------------|
| 1 | endpoints.py | -80 | chore: remove unused CapacityRetryTracker class |
| 2 | interceptor.py | -2 | refactor: remove duplicate get_config() in 429 block |
| 3 | interceptor.py, endpoints.py | -5 | chore: remove dead and redundant imports |
| 4 | interceptor.py | +12 | feat: mark endpoints as failed on server errors |
| 5 | interceptor.py | +2/-1 | refactor: import is_claude_model from source module |
| 6 | interceptor.py | +10/-10 | refactor: move safe imports to module level |

6 commits, net ~-70 lines. Two files touched. All independent — can be dispatched as a batch.
