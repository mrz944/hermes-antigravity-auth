# Antigravity Gap Remediation Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Close the concrete gaps found in the May 28 project review: credential/package safety, config reliability, account-rotation correctness, Claude tool IDs, auth-store invariants, debug-log privacy, and documentation drift.

**Architecture:** Keep the current Hermes Cloud Code architecture. Body-shaping remains in the `wrap_code_assist_request` patch; httpx request hooks remain headers/account/token only; response hooks perform side effects for 401/403/429/5xx. Treat credential state as a three-store invariant: `antigravity-accounts.json`, `auth.json`, and `auth/google_oauth.json`.

**Tech Stack:** Python 3.10+, pytest/unittest, stdlib-first, optional PyYAML, setuptools build hooks, Hermes plugin wrappers, httpx event hooks.

---

## Architecture/Safety Audit Before Implementation

Read and preserve these invariants from `docs/ARCHITECTURE.md`, `docs/ANTIGRAVITY_API_SPEC.md`, and the Hermes plugin skill references:

1. Runtime heartbeat:
   `hermes_plugin.register()` -> `interceptor.install()` -> `GeminiCloudCodeClient.__init__` monkey patch -> `wrap_code_assist_request` patch -> httpx request/response hooks.
2. Request hook invariant: it reads the envelope for model/family only; it must not rewrite request body bytes.
3. Claude body transforms belong in the wrapper patch, not the request hook.
4. Critical headers to preserve or intentionally replace: `Authorization`, `Content-Type`, `Host`, `Accept`, `Accept-Encoding`, `Content-Length`.
5. Provider wrapper `from antigravity_auth.hermes_provider_plugin import *` is intentional because module-level side effects register aliases and patch model pickers. Do not replace it with explicit imports.
6. Response hook imports are broad-try/except and can silently disable account rotation on import errors. Every task touching `interceptor.py`, `accounts/manager.py`, `accounts/ratelimit.py`, `accounts/rotation.py`, or `accounts/state.py` needs manual import-chain verification.
7. API spec confirms function-call IDs live inside nested `functionCall` / `functionResponse`, not at Gemini Part level.
8. API spec confirms Antigravity SSE is Gemini-shaped `data: {"response": ...}`; if this project claims OpenAI streaming conversion, tests must prove it.

Manual import-chain probe to run after every heartbeat task:

```bash
python3 -c "from antigravity_auth.interceptor import install; print('interceptor OK')"
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('manager OK')"
python3 -c "from antigravity_auth.accounts.ratelimit import mark_rate_limited, mark_rate_limited_with_reason; print('ratelimit OK')"
python3 - <<'PY'
import sys, types
class FakeProviderProfile:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
providers = types.ModuleType("providers")
providers.register_provider = lambda profile: None
providers_base = types.ModuleType("providers.base")
providers_base.ProviderProfile = FakeProviderProfile
sys.modules["providers"] = providers
sys.modules["providers.base"] = providers_base
from antigravity_auth.hermes_provider_plugin import antigravity
print("provider OK")
PY
```

Expected output: each probe prints an `OK` line. The provider probe injects fake Hermes `providers` modules so it works in local dev/CI as well as inside Hermes runtime.

Execution rule: do not dispatch these tasks in parallel. Several tasks touch the same runtime chain and git commits would race. Execute sequentially with spec review and quality review after each high-risk task.

Local secret warning: this checkout currently has ignored `antigravity_auth/_credentials.py`. Do not read, print, or commit it. Packaging-verification commands that build a wheel should either run in CI/clean checkout or after the controller moves local credentials out of the package tree.

---

## Phase 1 — Packaging and Credential Safety

### Task 1: Add a package build guard for local `_credentials.py`

**Risk:** LOW for runtime; HIGH for release safety.

**Objective:** Make source distribution/wheel builds fail if a local ignored credential module exists under `antigravity_auth/`.

**Files:**
- Create: `setup.py`
- Create: `antigravity_auth/packaging_guard.py`
- Test: `antigravity_auth/test_packaging_guard.py`
- Modify: `.github/workflows/ci.yml:20-25`, `.github/workflows/ci.yml:35-40`

**Step 1: Write failing tests**

Create `antigravity_auth/test_packaging_guard.py`:

```python
import tempfile
import unittest
from pathlib import Path

from antigravity_auth.packaging_guard import assert_no_local_credentials_module


class TestPackagingGuard(unittest.TestCase):
  def test_allows_tree_without_local_credentials_module(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      (root / "antigravity_auth").mkdir()
      assert_no_local_credentials_module(root)

  def test_rejects_tree_with_local_credentials_module(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      package_dir = root / "antigravity_auth"
      package_dir.mkdir()
      (package_dir / "_credentials.py").write_text("SECRET = 'x'\n", encoding="utf-8")

      with self.assertRaisesRegex(RuntimeError, "Refusing to build"):
        assert_no_local_credentials_module(root)
```

**Step 2: Run test to verify RED**

Run:

```bash
python3 -m pytest antigravity_auth/test_packaging_guard.py -v
```

Expected: FAIL with `ModuleNotFoundError` or import failure because `antigravity_auth.packaging_guard` does not exist.

**Step 3: Write minimal implementation**

Create `antigravity_auth/packaging_guard.py`:

```python
"""Release-safety checks for package builds."""
from __future__ import annotations

from pathlib import Path


_LOCAL_CREDENTIALS_RELATIVE = Path("antigravity_auth") / "_credentials.py"


def assert_no_local_credentials_module(root: str | Path | None = None) -> None:
  """Raise if the gitignored local credentials module would be packaged."""
  base = Path(root) if root is not None else Path(__file__).resolve().parents[1]
  credentials_path = base / _LOCAL_CREDENTIALS_RELATIVE
  if credentials_path.exists():
    raise RuntimeError(
      "Refusing to build with local antigravity_auth/_credentials.py present. "
      "Move credentials to environment variables or ~/.hermes/antigravity-credentials.json "
      "before building a wheel/sdist."
    )
```

Create `setup.py`:

```python
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist


def _assert_safe_build_tree() -> None:
  from antigravity_auth.packaging_guard import assert_no_local_credentials_module

  assert_no_local_credentials_module(Path(__file__).parent)


class build_py(_build_py):
  def run(self):
    _assert_safe_build_tree()
    super().run()


class sdist(_sdist):
  def run(self):
    _assert_safe_build_tree()
    super().run()


setup(cmdclass={"build_py": build_py, "sdist": sdist})
```

**Step 4: Add CI artifact inspection**

Modify `.github/workflows/ci.yml` by adding a package-artifact step after tests in the `clean-install` job:

```yaml
      - name: Build package artifacts and inspect contents
        run: |
          python -m pip install build
          python -m build --sdist --wheel
          python - <<'PY'
          import tarfile
          import zipfile
          from pathlib import Path

          forbidden = "antigravity_auth/_credentials.py"
          for artifact in Path("dist").iterdir():
              names = []
              if artifact.suffix == ".whl":
                  with zipfile.ZipFile(artifact) as z:
                      names = z.namelist()
              elif artifact.suffixes[-2:] == [".tar", ".gz"]:
                  with tarfile.open(artifact) as t:
                      names = [m.name for m in t.getmembers()]
              if any(name.endswith(forbidden) for name in names):
                  raise SystemExit(f"Forbidden credential module in {artifact}")
          print("artifact contents OK")
          PY
```

**Step 5: Run GREEN tests**

Run:

```bash
python3 -m pytest antigravity_auth/test_packaging_guard.py -v
python3 -m pytest antigravity_auth/ -q
python3 -m compileall -q antigravity_auth plugins
```

Expected: new test passes; full suite passes; compileall returns no output.

**Step 6: Manual build verification**

In a tree that deliberately contains a dummy local credential file, verify the guard blocks both sdist and wheel paths:

```bash
tmp=$(mktemp -d)
rsync -a \
  --exclude '.git' \
  --exclude 'dist' \
  --exclude '.pytest_cache' \
  --exclude 'hermes_antigravity_auth.egg-info' \
  --exclude '__pycache__' \
  --exclude 'antigravity_auth/_credentials.py' \
  ./ "$tmp"/
cd "$tmp"
python3 -m pip install build
cat > antigravity_auth/_credentials.py <<'PY'
ANTIGRAVITY_CLIENT_ID = "dummy"
ANTIGRAVITY_CLIENT_SECRET = "dummy"
PY
python3 -m build --sdist >/tmp/ag-sdist.log 2>&1 && { cat /tmp/ag-sdist.log; exit 1; } || grep -q "Refusing to build" /tmp/ag-sdist.log
python3 -m build --wheel >/tmp/ag-wheel.log 2>&1 && { cat /tmp/ag-wheel.log; exit 1; } || grep -q "Refusing to build" /tmp/ag-wheel.log
printf 'build guard rejection OK\n'
```

Expected: `build guard rejection OK`.

In a clean checkout or after moving local credentials out of `antigravity_auth/`, run:

```bash
python3 -m pip install build
python3 -m build --sdist --wheel
python3 - <<'PY'
from pathlib import Path
import tarfile
import zipfile
for artifact in Path('dist').iterdir():
    if artifact.suffix == '.whl':
        with zipfile.ZipFile(artifact) as z:
            names = z.namelist()
    else:
        with tarfile.open(artifact) as t:
            names = [m.name for m in t.getmembers()]
    assert not any(n.endswith('antigravity_auth/_credentials.py') for n in names), artifact
print('artifact credential check OK')
PY
```

Expected: `artifact credential check OK`.

**Step 7: Commit**

```bash
git add setup.py antigravity_auth/packaging_guard.py antigravity_auth/test_packaging_guard.py .github/workflows/ci.yml
git commit -m "build: prevent packaging local antigravity credentials"
```

---

### Task 2: Move credential resolution to a safe shared resolver

**Risk:** MODERATE. Touches login/provider credential bootstrap but not the request hook.

**Objective:** Resolve OAuth client credentials from env vars first, then an external Hermes-home JSON file; avoid depending on package-local `_credentials.py` for normal operation.

**Files:**
- Create: `antigravity_auth/credentials.py`
- Test: `antigravity_auth/test_credentials.py`
- Modify: `antigravity_auth/constants.py:5-41`
- Modify: `antigravity_auth/hermes_provider_plugin.py:15-35`

**Step 1: Write failing tests**

Create `antigravity_auth/test_credentials.py`:

```python
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from antigravity_auth.credentials import resolve_oauth_credentials


class TestResolveOAuthCredentials(unittest.TestCase):
  def test_env_values_win(self):
    env = {
      "ANTIGRAVITY_CLIENT_ID": "env-id",
      "ANTIGRAVITY_CLIENT_SECRET": "env-secret",
    }
    with patch.dict(os.environ, env, clear=True):
      self.assertEqual(resolve_oauth_credentials(), ("env-id", "env-secret"))

  def test_external_file_fills_missing_env_secret(self):
    with tempfile.TemporaryDirectory() as tmp:
      creds = Path(tmp) / "creds.json"
      creds.write_text(json.dumps({
        "client_id": "file-id",
        "client_secret": "file-secret",
      }), encoding="utf-8")
      env = {
        "ANTIGRAVITY_CLIENT_ID": "env-id",
        "HERMES_ANTIGRAVITY_CREDENTIALS_FILE": str(creds),
      }
      with patch.dict(os.environ, env, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("env-id", "file-secret"))

  def test_missing_returns_empty_strings(self):
    with tempfile.TemporaryDirectory() as tmp:
      env = {"HERMES_HOME": tmp}
      with patch.dict(os.environ, env, clear=True):
        self.assertEqual(resolve_oauth_credentials(), ("", ""))
```

**Step 2: Run test to verify RED**

Run:

```bash
python3 -m pytest antigravity_auth/test_credentials.py -v
```

Expected: FAIL because `antigravity_auth.credentials` does not exist.

**Step 3: Write implementation**

Create `antigravity_auth/credentials.py`:

```python
"""OAuth client credential resolution."""
from __future__ import annotations

import json
import os
from pathlib import Path


def _hermes_home() -> Path:
  return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()


def _credential_file_path() -> Path:
  override = os.environ.get("HERMES_ANTIGRAVITY_CREDENTIALS_FILE", "").strip()
  if override:
    return Path(override).expanduser()
  return _hermes_home() / "antigravity-credentials.json"


def _load_file_credentials() -> tuple[str, str]:
  path = _credential_file_path()
  if not path.exists():
    return ("", "")
  try:
    with open(path, "r", encoding="utf-8") as f:
      data = json.load(f)
    if not isinstance(data, dict):
      return ("", "")
    client_id = str(data.get("client_id") or data.get("ANTIGRAVITY_CLIENT_ID") or "").strip()
    client_secret = str(data.get("client_secret") or data.get("ANTIGRAVITY_CLIENT_SECRET") or "").strip()
    return (client_id, client_secret)
  except Exception:
    return ("", "")


def resolve_oauth_credentials() -> tuple[str, str]:
  """Resolve OAuth client ID/secret from env, then external Hermes credentials file."""
  env_id = os.environ.get("ANTIGRAVITY_CLIENT_ID", "").strip()
  env_secret = os.environ.get("ANTIGRAVITY_CLIENT_SECRET", "").strip()
  file_id, file_secret = _load_file_credentials()
  return (env_id or file_id, env_secret or file_secret)
```

Modify `antigravity_auth/constants.py` lines 5-21 to:

```python
from .credentials import resolve_oauth_credentials

ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET = resolve_oauth_credentials()
```

Keep `_MISSING_CREDENTIALS_ERROR`, but update option 2 text to:

```python
"  2. Create ~/.hermes/antigravity-credentials.json with client_id/client_secret\n"
```

Modify `antigravity_auth/hermes_provider_plugin.py` lines 21-30 inside `_set_oauth_env_from_credentials()` to:

```python
  if not client_id or not client_secret:
    try:
      from .credentials import resolve_oauth_credentials
      resolved_id, resolved_secret = resolve_oauth_credentials()
      client_id = client_id or resolved_id
      client_secret = client_secret or resolved_secret
    except Exception:
      pass
```

**Step 4: Run tests and import probes**

Run:

```bash
python3 -m pytest antigravity_auth/test_credentials.py antigravity_auth/test_oauth.py antigravity_auth/test_hermes_plugin.py -v
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.constants import require_credentials; print('constants OK')"
python3 - <<'PY'
import sys, types
class FakeProviderProfile:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
providers = types.ModuleType("providers")
providers.register_provider = lambda profile: None
providers_base = types.ModuleType("providers.base")
providers_base.ProviderProfile = FakeProviderProfile
sys.modules["providers"] = providers
sys.modules["providers.base"] = providers_base
from antigravity_auth.hermes_provider_plugin import antigravity
print("provider OK")
PY
```

Expected: targeted tests pass; full suite passes; import probes print `constants OK` and `provider OK`.

**Step 5: Commit**

```bash
git add antigravity_auth/credentials.py antigravity_auth/test_credentials.py antigravity_auth/constants.py antigravity_auth/hermes_provider_plugin.py
git commit -m "fix: resolve oauth credentials outside package tree"
```

---

### Task 3: Update credential and redirect documentation

**Risk:** LOW.

**Objective:** Align README, migration guide, and example docs with safe credential loading and the real OAuth redirect URI.

**Files:**
- Modify: `README.md:58-78`, `README.md:140-166`, `README.md:262-294`
- Modify: `MIGRATION.md:24-40`, `MIGRATION.md:85-90`
- Modify: `docs/ARCHITECTURE.md:65-69`
- Modify: `antigravity_auth/_credentials.py.example:1-21`

**Step 1: Write docs-only patch**

Replace the README credential section with:

````markdown
### 2. Provide OAuth client credentials

Source/git installs do not include private OAuth client credentials. Before
running `hermes antigravity login`, create a Google OAuth desktop client with
authorized redirect URI `http://localhost:51121/oauth-callback`, then provide
its values via environment variables:

```bash
export ANTIGRAVITY_CLIENT_ID="your-client-id.apps.googleusercontent.com"
export ANTIGRAVITY_CLIENT_SECRET="your-client-secret"
```

Or use an external Hermes credentials file outside the Python package tree:

```bash
mkdir -p ~/.hermes
cat > ~/.hermes/antigravity-credentials.json <<'JSON'
{
  "client_id": "your-client-id.apps.googleusercontent.com",
  "client_secret": "your-client-secret"
}
JSON
chmod 600 ~/.hermes/antigravity-credentials.json
```

Do not place real credentials in `antigravity_auth/_credentials.py`; local files
inside the package tree are refused by package builds to prevent wheel/sdist leaks.
````

Update `antigravity_auth/_credentials.py.example` line 17 to document the real callback:

```text
#   3. Add http://localhost:51121/oauth-callback as an authorized redirect URI
```

Also update the top comments to say it is legacy reference only, not the recommended location.

Update `MIGRATION.md` install step to use the GitHub/source path from README, not unqualified PyPI, and add the credential step before `login`.

**Step 2: Verify docs references**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
checks = {
  'README.md': ['~/.hermes/antigravity-credentials.json', 'localhost:51121/oauth-callback'],
  'MIGRATION.md': ['ANTIGRAVITY_CLIENT_ID', 'hermes-antigravity-install'],
  'docs/ARCHITECTURE.md': ['antigravity-credentials.json'],
  'antigravity_auth/_credentials.py.example': ['localhost:51121/oauth-callback'],
}
for path, needles in checks.items():
    text = Path(path).read_text(encoding='utf-8')
    missing = [needle for needle in needles if needle not in text]
    if missing:
        raise SystemExit(f'{path} missing {missing}')
print('docs credential references OK')
PY
```

Expected: `docs credential references OK`.

**Step 3: Commit**

```bash
git add README.md MIGRATION.md docs/ARCHITECTURE.md antigravity_auth/_credentials.py.example
git commit -m "docs: document safe antigravity credential setup"
```

---

### Task 4: Make optional YAML config failure visible and covered

**Risk:** LOW/MODERATE. Touches config loading but not runtime hooks.

**Objective:** Keep PyYAML optional, but warn when `config.yaml` exists and cannot be parsed because PyYAML is absent; update docs/CI so normal installs include YAML support when config is used.

**Files:**
- Modify: `antigravity_auth/config.py:169-176`
- Modify: `antigravity_auth/test_config.py`
- Modify: `README.md:46-56`, `README.md:142-165`
- Modify: `.github/workflows/ci.yml:20-23`

**Step 1: Write failing test**

Add to `antigravity_auth/test_config.py`:

```python
    def test_existing_yaml_without_pyyaml_warns(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from antigravity_auth import config as config_module

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("plugins:\n  entries:\n    antigravity:\n      debug: true\n", encoding="utf-8")
            with patch.dict("sys.modules", {"yaml": None}), \
                 patch.object(config_module.logger, "warning") as warning:
                result = config_module.load_config_from_yaml(path)

        self.assertIsNone(result)
        warning.assert_called()
```

If `config.py` does not yet have a module logger, this test should fail for that reason.

**Step 2: Implement warning**

Add near imports in `antigravity_auth/config.py`:

```python
import logging

logger = logging.getLogger(__name__)
```

Change the `except ImportError` block in `load_config_from_yaml()` to:

```python
  except ImportError:
    logger.warning(
      "Ignoring %s because PyYAML is not installed. Install with hermes-antigravity-auth[yaml] "
      "or pip install pyyaml to enable YAML configuration.",
      yaml_path,
    )
    return None
```

**Step 3: Update install docs and CI**

Update README install commands:

```bash
pip install "hermes-antigravity-auth[yaml] @ git+https://github.com/Reedtrullz/hermes-antigravity-auth.git"
```

For editable source:

```bash
pip install -e ".[dev,yaml]"
```

Update `.github/workflows/ci.yml` install line to:

```yaml
        run: python -m pip install -e '.[dev,yaml]'
```

Keep an optional no-yaml test path if desired, but do not allow all config tests to skip in the main CI job.

**Step 4: Run tests**

Run:

```bash
python3 -m pytest antigravity_auth/test_config.py -v
python3 -m pytest antigravity_auth/ -q
```

Expected: config tests pass; full suite passes with no YAML config skips in an environment with `[yaml]` installed.

**Step 5: Commit**

```bash
git add antigravity_auth/config.py antigravity_auth/test_config.py README.md .github/workflows/ci.yml
git commit -m "fix: warn when yaml config cannot be loaded"
```

---

## Phase 2 — Runtime Account and Auth Correctness

### Task 5: Add safe account lookup by selected request index

**Risk:** HIGH. Touches the account manager in the response-hook import chain.

**Objective:** Provide a public `AccountManager.get_account_by_index()` helper so response hooks can act on the concrete account that sent the request.

**Files:**
- Modify: `antigravity_auth/accounts/manager.py:174-183`
- Modify: `antigravity_auth/accounts/test_manager.py`

**Step 1: Write failing tests**

Add to `antigravity_auth/accounts/test_manager.py` in the account-manager-with-accounts test class:

```python
    def test_get_account_by_index_returns_enabled_account(self):
        data = {
            "version": 4,
            "accounts": [
                {"email": "alice@example.com", "refreshToken": "refresh-alice", "projectId": "proj-a"},
                {"email": "bob@example.com", "refreshToken": "refresh-bob", "projectId": "proj-b"},
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        self.assertEqual(manager.get_account_by_index(0).email, "alice@example.com")
        self.assertEqual(manager.get_account_by_index(1).email, "bob@example.com")

    def test_get_account_by_index_rejects_out_of_range_and_disabled(self):
        data = {
            "version": 4,
            "accounts": [
                {"email": "alice@example.com", "refreshToken": "refresh-alice", "projectId": "proj-a"},
            ],
            "activeIndex": 0,
            "cursor": 0,
            "activeIndexByFamily": {"claude": 0, "gemini": 0},
        }
        manager = self._make_manager(data)
        self.assertIsNone(manager.get_account_by_index(-1))
        self.assertIsNone(manager.get_account_by_index(999))
        manager.set_account_enabled(0, False)
        self.assertIsNone(manager.get_account_by_index(0))
```

**Step 2: Verify RED**

Run:

```bash
python3 -m pytest antigravity_auth/accounts/test_manager.py -k get_account_by_index -v
```

Expected: FAIL because `get_account_by_index` does not exist.

**Step 3: Implement helper**

Add after `get_current_account_for_family()` in `antigravity_auth/accounts/manager.py`:

```python
  def get_account_by_index(self, account_index: int) -> ManagedAccount | None:
    if not isinstance(account_index, int) or isinstance(account_index, bool):
      return None
    if 0 <= account_index < len(self._accounts):
      account = self._accounts[account_index]
      if account.enabled is not False:
        return account
    return None
```

**Step 4: Verify GREEN and import chain**

Run:

```bash
python3 -m pytest antigravity_auth/accounts/test_manager.py -k get_account_by_index -v
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('manager OK')"
python3 -c "from antigravity_auth.accounts.ratelimit import mark_rate_limited; print('ratelimit OK')"
python3 -c "from antigravity_auth.interceptor import install; print('interceptor OK')"
```

Expected: tests pass; probes print OK.

**Step 5: Commit**

```bash
git add antigravity_auth/accounts/manager.py antigravity_auth/accounts/test_manager.py
git commit -m "fix: expose safe account lookup by index"
```

---

### Task 6: Use selected request account for 403 and 429 handling

**Risk:** HIGH. Touches `interceptor.py` response hook.

**Objective:** Mark/cool down the account stored in `request.extensions["antigravity_selected_account_index"]`, not whichever account is current when the response arrives.

**Files:**
- Modify: `antigravity_auth/interceptor.py:427-577`
- Modify: `antigravity_auth/test_interceptor.py:485-603`

**Step 1: Write failing tests**

Add two tests to `antigravity_auth/test_interceptor.py` near existing 403/429 tests:

```python
    def test_429_marks_selected_account_not_current_account(self):
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeAccount:
            def __init__(self, index):
                self.index = index
                self.rate_limit_reset_times = type("RateLimits", (), {})()

        class FakeManager:
            def __init__(self):
                self.current = FakeAccount(1)
                self.selected = FakeAccount(0)
                self.marked = None
            def get_current_account_for_family(self, family):
                return self.current
            def get_account_by_index(self, index):
                return self.selected if index == 0 else None
            def get_current_or_next_for_family(self, family, **kwargs):
                return self.selected
            def save_to_disk(self):
                return True

        config = type("Config", (), {
            "proactive_token_refresh": False,
            "switch_on_first_rate_limit": True,
            "default_retry_after_seconds": 10,
            "cli_first": False,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
        })()
        mgr = FakeManager()
        response = self._make_response(model="claude-sonnet-4-6", status=429, header_style="antigravity")
        response.request.extensions["antigravity_selected_account_index"] = 0
        marked = []

        with patch("antigravity_auth.config.get_config", return_value=config), \
             patch("antigravity_auth.accounts.manager.get_or_create_global_manager", return_value=mgr), \
             patch("antigravity_auth.accounts.ratelimit.mark_rate_limited", side_effect=lambda account, *args: marked.append(account.index)):
            _antigravity_response_hook(response)

        self.assertEqual(marked, [0])

    def test_403_cools_selected_account_not_current_account(self):
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeAccount:
            def __init__(self, index):
                self.index = index
                self.cooling_down_until = None
                self.cooldown_reason = None
                self.refresh_parts = type("Refresh", (), {"refresh_token": "r", "project_id": "p", "managed_project_id": "m"})()
                self.email = "user@example.com"

        class FakeManager:
            def __init__(self):
                self.current = FakeAccount(1)
                self.selected = FakeAccount(0)
            def get_current_account_for_family(self, family):
                return self.current
            def get_account_by_index(self, index):
                return self.selected if index == 0 else None
            def get_current_or_next_for_family(self, family, **kwargs):
                return self.selected
            def save_to_disk(self):
                return True

        config = type("Config", (), {
            "proactive_token_refresh": False,
            "switch_on_first_rate_limit": True,
            "default_retry_after_seconds": 10,
            "cli_first": False,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
        })()
        mgr = FakeManager()
        response = self._make_response(model="claude-sonnet-4-6", status=403, header_style="antigravity")
        response.request.extensions["antigravity_selected_account_index"] = 0

        with patch("antigravity_auth.config.get_config", return_value=config), \
             patch("antigravity_auth.accounts.manager.get_or_create_global_manager", return_value=mgr), \
             patch("antigravity_auth.token.refresh_access_token", return_value={}):
            _antigravity_response_hook(response)

        self.assertEqual(mgr.selected.cooldown_reason, "auth-failure")
        self.assertIsNone(mgr.current.cooldown_reason)
```

**Step 2: Verify RED**

Run:

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -k "selected_account_not_current" -v
```

Expected: FAIL because the current implementation marks/cools `current`.

**Step 3: Implement helper in `interceptor.py`**

Add near `_request_model_from_response()` or before `_antigravity_response_hook()`:

```python
def _response_account_for_request(mgr, request_extensions: dict, family: str):
    selected_idx = request_extensions.get("antigravity_selected_account_index")
    if isinstance(selected_idx, int) and not isinstance(selected_idx, bool):
        try:
            selected = mgr.get_account_by_index(selected_idx)
        except AttributeError:
            selected = None
        if selected is not None:
            return selected
    return mgr.get_current_account_for_family(family)
```

Change both 403 and 429 blocks from:

```python
active = mgr.get_current_account_for_family(family)
```

to:

```python
active = _response_account_for_request(mgr, request_extensions, family)
```

**Step 4: Verify GREEN and import chain**

Run:

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -k "selected_account_not_current or rotation_uses_configured_selection_context" -v
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.interceptor import install; print('interceptor OK')"
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('manager OK')"
python3 -c "from antigravity_auth.accounts.ratelimit import mark_rate_limited; print('ratelimit OK')"
```

Expected: targeted tests and full suite pass; probes print OK.

**Step 5: Commit**

```bash
git add antigravity_auth/interceptor.py antigravity_auth/test_interceptor.py
git commit -m "fix: apply response rate limits to selected account"
```

---

### Task 7: Use reason-aware 429 backoff in the response hook

**Risk:** HIGH. Touches response-hook account rotation.

**Objective:** Route 429 handling through `AccountManager.mark_rate_limited_with_reason()` so runtime behavior uses the tested reason/backoff model.

**Files:**
- Modify: `antigravity_auth/interceptor.py:528-541`
- Modify: `antigravity_auth/test_interceptor.py`

**Step 1: Write failing test**

Add to `antigravity_auth/test_interceptor.py`:

```python
    def test_429_uses_reason_aware_backoff(self):
        from antigravity_auth.interceptor import _antigravity_response_hook

        class FakeAccount:
            index = 0

        class FakeManager:
            def __init__(self):
                self.account = FakeAccount()
                self.reason_call = None
            def get_account_by_index(self, index):
                return self.account
            def get_current_account_for_family(self, family):
                return self.account
            def mark_rate_limited_with_reason(
                self,
                account,
                family,
                header_style,
                model,
                reason,
                retry_after_ms=None,
                failure_ttl_ms=3600_000,
            ):
                self.reason_call = {
                    "account_index": account.index,
                    "family": family,
                    "header_style": header_style,
                    "model": model,
                    "reason": reason,
                    "retry_after_ms": retry_after_ms,
                }
                return 3957.0
            def get_current_or_next_for_family(self, family, **kwargs):
                return self.account
            def save_to_disk(self):
                return True

        config = type("Config", (), {
            "proactive_token_refresh": False,
            "switch_on_first_rate_limit": True,
            "default_retry_after_seconds": 10,
            "cli_first": False,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
        })()
        mgr = FakeManager()
        body = {
            "error": {
                "code": 429,
                "message": "You have exhausted your capacity on this model. Your quota will reset after 3s.",
                "status": "RESOURCE_EXHAUSTED",
                "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3.957525076s"}],
            }
        }
        response = self._make_response(model="claude-sonnet-4-6", status=429, header_style="antigravity", json_body=body)
        response.request.extensions["antigravity_selected_account_index"] = 0

        with patch("antigravity_auth.config.get_config", return_value=config), \
             patch("antigravity_auth.accounts.manager.get_or_create_global_manager", return_value=mgr):
            _antigravity_response_hook(response)

        self.assertEqual(mgr.reason_call["account_index"], 0)
        self.assertEqual(mgr.reason_call["reason"], "MODEL_CAPACITY_EXHAUSTED")
        self.assertAlmostEqual(mgr.reason_call["retry_after_ms"], 3957.0, places=3)
```

If `_make_response` does not support `json_body`, extend the test helper in the same file with a default-safe argument.

**Step 2: Verify RED**

Run:

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -k reason_aware_backoff -v
```

Expected: FAIL because current hook calls standalone `mark_rate_limited()`.

**Step 3: Implement reason extraction and call manager method**

Inside the 429 block, replace the standalone import/call with:

```python
            retry_after_ms = float(config.default_retry_after_seconds * 1000)
            rh = response.headers.get("Retry-After") or response.headers.get("retry-after")
            if rh:
                try:
                    retry_after_ms = float(rh) * 1000
                except ValueError:
                    pass

            message = None
            raw_reason = None
            try:
                from .transform.response import extract_retry_info
                parsed = response.json()
                if isinstance(parsed, dict):
                    error = parsed.get("error")
                    if isinstance(error, dict):
                        raw_message = error.get("message")
                        if isinstance(raw_message, str):
                            message = raw_message
                        status_value = error.get("status")
                        if isinstance(status_value, str):
                            raw_reason = status_value
                    retry_info = extract_retry_info(parsed)
                    if retry_info and retry_info.get("retryDelayMs") is not None:
                        retry_after_ms = float(retry_info["retryDelayMs"])
            except Exception:
                pass

            from .accounts.ratelimit import mark_rate_limited, parse_rate_limit_reason
            parsed_reason = parse_rate_limit_reason(raw_reason, message, response.status_code)

            if hasattr(mgr, "mark_rate_limited_with_reason"):
                mgr.mark_rate_limited_with_reason(
                    active,
                    family,
                    header_style,
                    model,
                    parsed_reason,
                    retry_after_ms=retry_after_ms,
                )
            else:
                mark_rate_limited(active, retry_after_ms, family, header_style, model)
```

Confirm `extract_retry_info()` returns the key name used above. If it currently returns another shape, adapt the test and call to the existing function rather than adding a duplicate parser.

**Step 4: Verify GREEN and import chain**

Run:

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -k "reason_aware_backoff or selected_account_not_current" -v
python3 -m pytest antigravity_auth/accounts/test_ratelimit.py antigravity_auth/test_interceptor.py -q
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.interceptor import install; print('interceptor OK')"
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('manager OK')"
python3 -c "from antigravity_auth.accounts.ratelimit import mark_rate_limited_with_reason; print('ratelimit OK')"
```

Expected: all pass and probes print OK.

**Step 5: Commit**

```bash
git add antigravity_auth/interceptor.py antigravity_auth/test_interceptor.py
git commit -m "fix: use reason-aware rate-limit backoff at runtime"
```

---

### Task 8: Preserve content-array tool IDs in message transforms

**Risk:** MODERATE. Utility transform path; affects direct OpenAI-to-Gemini conversions and Claude tool pairing.

**Objective:** Preserve Anthropic-style `tool_use.id` and `tool_result.tool_use_id` when converting content-array parts to Gemini nested `functionCall` / `functionResponse` objects.

**Files:**
- Modify: `antigravity_auth/transform/messages.py:29-67`, `antigravity_auth/transform/messages.py:70-88`
- Modify: `antigravity_auth/transform/test_messages.py:332-368`

**Step 1: Write/update failing tests**

Change `test_tool_use_content_part_type` expected value to include the ID:

```python
      {"functionCall": {"name": "get_weather", "args": {"city": "Tokyo"}, "id": "tu1"}},
```

Add a direct content-array tool result test:

```python
  def test_tool_result_content_part_preserves_tool_use_id(self):
    messages = [
      {"role": "assistant", "content": [
        {"type": "tool_use", "id": "tu1", "name": "get_weather", "input": {"city": "Tokyo"}},
      ]},
      {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "tu1", "content": "sunny"},
      ]},
    ]
    contents, _ = transform_messages_to_contents(messages)
    self.assertEqual(contents[1]["parts"][0], {"functionResponse": {
      "name": "get_weather",
      "id": "tu1",
      "response": {"content": "sunny"},
    }})
```

**Step 2: Verify RED**

Run:

```bash
python3 -m pytest antigravity_auth/transform/test_messages.py -k "content_part" -v
```

Expected: FAIL because IDs/name recovery are currently missing for content-array parts.

**Step 3: Implement ID preservation**

Change `_convert_content_part()` signature:

```python
def _convert_content_part(part: dict, tool_call_id_to_name: dict[str, str] | None = None) -> dict | None:
```

In `tool_use` branch:

```python
    function_call = {"name": name, "args": args}
    tool_id = part.get("id")
    if tool_id:
      function_call["id"] = str(tool_id)
      if name and tool_call_id_to_name is not None:
        tool_call_id_to_name[str(tool_id)] = str(name)
    return {"functionCall": function_call}
```

In `tool_result` branch:

```python
    result_id = part.get("tool_use_id") or part.get("id")
    if not name and result_id and tool_call_id_to_name is not None:
      name = tool_call_id_to_name.get(str(result_id), "")
    function_response = {"name": name, "response": {"content": content}}
    if result_id:
      function_response["id"] = str(result_id)
    return {"functionResponse": function_response}
```

Change `_content_to_parts()` signature:

```python
def _content_to_parts(content: str | list | None, tool_call_id_to_name: dict[str, str] | None = None) -> list[dict]:
```

Pass the map when converting dict content items:

```python
        converted = _convert_content_part(item, tool_call_id_to_name)
```

Update `transform_messages_to_contents()` call sites so the shared `tool_call_id_to_name` map is passed into `_content_to_parts(...)` for message content arrays.

**Step 4: Verify GREEN**

Run:

```bash
python3 -m pytest antigravity_auth/transform/test_messages.py -k "content_part or tool_call_id_preserved" -v
python3 -m pytest antigravity_auth/transform/ -q
python3 -m pytest antigravity_auth/ -q
```

Expected: targeted and full tests pass.

**Step 5: Commit**

```bash
git add antigravity_auth/transform/messages.py antigravity_auth/transform/test_messages.py
git commit -m "fix: preserve content-array tool call ids"
```

---

### Task 9: Fail closed when request-time account selection cannot provide a token

**Risk:** HIGH. Touches request hook authorization behavior.

**Objective:** Avoid silently sending a request with stale pre-existing `Authorization` when account selection was attempted but no selected token is available.

**Files:**
- Modify: `antigravity_auth/auth_sync.py:57-79`
- Modify: `antigravity_auth/interceptor.py:155-164`, `antigravity_auth/interceptor.py:398-423`
- Modify: `antigravity_auth/test_interceptor.py`
- Modify: `antigravity_auth/test_storage.py` or new auth-sync tests if needed

**Step 1: Write failing request-hook test**

Add to `antigravity_auth/test_interceptor.py`:

```python
    def test_request_hook_removes_stale_authorization_when_selection_fails(self):
        from antigravity_auth.interceptor import _antigravity_request_hook

        config = type("Config", (), {
            "cli_first": False,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
        })()
        request = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            headers={"Authorization": "Bearer stale", "Content-Type": "application/json"},
            json={"model": "claude-sonnet-4-6", "request": {"contents": []}},
        )

        with patch("antigravity_auth.config.get_config", return_value=config), \
             patch("antigravity_auth.interceptor._select_request_account", return_value=None):
            _antigravity_request_hook(request)

        self.assertNotEqual(request.headers.get("Authorization"), "Bearer stale")
        self.assertEqual(request.extensions.get("antigravity_account_selection_failed"), True)
```

**Step 2: Verify RED**

Run:

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -k stale_authorization -v
```

Expected: FAIL because stale Authorization is preserved.

**Step 3: Split auth sync result from outbound token availability**

In `antigravity_auth/auth_sync.py`, add:

```python
from dataclasses import dataclass


@dataclass
class AuthSyncResult:
  auth_json: bool
  google_oauth: bool

  @property
  def ok(self) -> bool:
    return self.auth_json and self.google_oauth
```

Change `sync_token_to_all_auth_stores()` to return `AuthSyncResult`. It should set `auth_json=False` only if `sync_token_to_auth_json()` raises; set `google_oauth=False` if native import/save fails.

Keep a compatibility helper if needed:

```python
def sync_token_to_all_auth_stores_bool(*args, **kwargs) -> bool:
  return sync_token_to_all_auth_stores(*args, **kwargs).ok
```

**Step 4: Change selection behavior**

In `_select_request_account()`, do not return `None` only because google_oauth sync failed. Log the partial failure and still return the refreshed access token:

```python
    sync_result = sync_token_to_all_auth_stores(
      access_token=refreshed["access"],
      refresh_token=sync_refresh,
      project_id=sync_project_id,
      email=account.email,
      expires_ms=refreshed.get("expires"),
      set_active=True,
    )
    if not getattr(sync_result, "auth_json", bool(sync_result)):
      return None
    if not getattr(sync_result, "google_oauth", bool(sync_result)):
      logger.warning("Native google_oauth sync failed; using selected access token for outbound request")
```

In `_antigravity_request_hook()`, if `selected` is falsey:

```python
    if selected and selected.get("access"):
        selected_index = selected.get("account_index")
        if type(selected_index) is int:
            request.extensions["antigravity_selected_account_index"] = selected_index
        request.headers["Authorization"] = f"Bearer {selected['access']}"
    else:
        request.extensions["antigravity_account_selection_failed"] = True
        if "Authorization" in request.headers:
            del request.headers["Authorization"]
```

This intentionally fails closed with a likely 401 rather than using the wrong account.

**Step 5: Verify GREEN and import chain**

Run:

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -k "stale_authorization or request_hook" -v
python3 -m pytest antigravity_auth/test_hermes_plugin.py antigravity_auth/test_storage.py antigravity_auth/test_interceptor.py -q
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.interceptor import install; print('interceptor OK')"
python3 -c "from antigravity_auth.auth_sync import sync_token_to_all_auth_stores; print('auth_sync OK')"
```

Expected: all pass and probes print OK.

**Step 6: Commit**

```bash
git add antigravity_auth/auth_sync.py antigravity_auth/interceptor.py antigravity_auth/test_interceptor.py antigravity_auth/test_storage.py
git commit -m "fix: avoid stale authorization after account selection failure"
```

---

### Task 10: Harden debug log permissions and token redaction

**Risk:** LOW/MODERATE.

**Objective:** Create debug logs as private files and redact common token forms in request/response body previews.

**Files:**
- Modify: `antigravity_auth/debug.py:80-188`
- Modify: `antigravity_auth/test_debug.py`

**Step 1: Write failing tests**

Add to `antigravity_auth/test_debug.py`:

```python
    def test_debug_log_file_is_private(self):
        import os
        import stat
        import tempfile
        from antigravity_auth.debug import initialize_debug, get_log_file_path

        with tempfile.TemporaryDirectory() as tmp:
            old_umask = os.umask(0o022)
            try:
                initialize_debug(True, log_dir=tmp)
            finally:
                os.umask(old_umask)
            path = get_log_file_path()
            self.assertIsNotNone(path)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_sanitize_body_redacts_camel_case_and_bearer(self):
        from antigravity_auth.debug import _sanitize_body

        body = '{"accessToken":"abc","refreshToken":"def","authorization":"Bearer xyz"}'
        sanitized = _sanitize_body(body)
        self.assertNotIn("abc", sanitized)
        self.assertNotIn("def", sanitized)
        self.assertNotIn("Bearer xyz", sanitized)
```

**Step 2: Verify RED**

Run:

```bash
python3 -m pytest antigravity_auth/test_debug.py -k "private or redacts_camel" -v
```

Expected: FAIL; file mode is currently 0644 and camelCase values are not redacted.

**Step 3: Implement secure opener/redaction**

In `debug.py`, add:

```python
def _private_log_opener(path: str, flags: int) -> int:
  return os.open(path, flags, 0o600)
```

Change `_get_logs_dir()` after mkdir:

```python
  try:
    os.chmod(logs_dir, 0o700)
  except Exception:
    pass
```

Change `_create_log_writer()` open call:

```python
    f = open(file_path, "a", encoding="utf-8", opener=_private_log_opener)
    try:
      os.chmod(file_path, 0o600)
    except Exception:
      pass
```

Expand `_sanitize_body()`:

```python
  for key in ("access_token", "refresh_token", "id_token", "accessToken", "refreshToken", "idToken"):
    body = re.sub(rf'"{key}"\s*:\s*"[^"]+"', f'"{key}":"[REDACTED]"', body)
  body = re.sub(r'Bearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [REDACTED]', body)
  return body
```

**Step 4: Verify GREEN**

Run:

```bash
python3 -m pytest antigravity_auth/test_debug.py -v
python3 -m pytest antigravity_auth/ -q
```

Expected: debug tests and full suite pass.

**Step 5: Commit**

```bash
git add antigravity_auth/debug.py antigravity_auth/test_debug.py
git commit -m "fix: write antigravity debug logs privately"
```

---

### Task 11: Persist invalid_grant cleanup for managed runtime refreshes

**Risk:** HIGH. Touches token/account/auth-store invariants.

**Objective:** Ensure revoked managed accounts are removed/rehomed when runtime refresh paths encounter `invalid_grant`.

**Files:**
- Modify: `antigravity_auth/interceptor.py:143`, `antigravity_auth/interceptor.py:467`, `antigravity_auth/interceptor.py:514`, `antigravity_auth/interceptor.py:565`
- Modify: `antigravity_auth/tools.py:50-55`
- Modify: `antigravity_auth/token_watchdog.py:80-85`
- Test: `antigravity_auth/test_interceptor.py`, `antigravity_auth/test_token.py`, `antigravity_auth/test_storage.py`

**Step 1: Write failing runtime test**

Add a test proving the interceptor passes `persist=True` for managed account refresh:

```python
    def test_request_account_refresh_uses_persist_true(self):
        from antigravity_auth.interceptor import _select_request_account

        class FakeAccount:
            index = 0
            email = "user@example.com"
            refresh_parts = type("Refresh", (), {"refresh_token": "r", "project_id": "p", "managed_project_id": "m"})()

        class FakeManager:
            def get_current_or_next_for_family(self, *args, **kwargs):
                return FakeAccount()
            def mark_account_used(self, index):
                pass
            def save_to_disk(self):
                return True

        config = type("Config", (), {
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
        })()
        calls = []

        with patch("antigravity_auth.accounts.shared.get_or_create_global_manager", return_value=FakeManager()), \
             patch("antigravity_auth.token.refresh_access_token", side_effect=lambda auth, **kw: calls.append(kw) or {"access": "a", "refresh": "r|p|m"}), \
             patch("antigravity_auth.auth_sync.sync_token_to_all_auth_stores", return_value=True):
            _select_request_account("claude-sonnet-4-6", "antigravity", config)

        self.assertEqual(calls[0].get("persist"), True)
```

**Step 2: Verify RED**

Run:

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -k persist_true -v
```

Expected: FAIL because refresh call omits `persist=True`.

**Step 3: Implement minimal changes**

For refreshes that use managed account storage, pass `persist=True`. Example in `_select_request_account()`:

```python
    refreshed = refresh_access_token(
      {"refresh": packed_refresh, "email": account.email},
      persist=True,
      set_active=True,
    )
```

For response-hook refreshes and tool/watchdog paths, pass `persist=True` and `set_active=True` only when that path is actually selecting the active runtime account. Do not pass `set_active=True` for side-effect-free health probes.

**Step 4: Verify storage invariant tests**

Run existing invalid_grant tests and add any missing case from `test_token.py`:

```bash
python3 -m pytest antigravity_auth/test_token.py -k invalid_grant -v
python3 -m pytest antigravity_auth/test_interceptor.py -k persist_true -v
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.token import refresh_access_token; print('token OK')"
python3 -c "from antigravity_auth.interceptor import install; print('interceptor OK')"
```

Expected: tests pass; probes print OK.

**Step 5: Commit**

```bash
git add antigravity_auth/interceptor.py antigravity_auth/tools.py antigravity_auth/token_watchdog.py antigravity_auth/test_interceptor.py antigravity_auth/test_token.py
git commit -m "fix: persist revoked-account cleanup in runtime refreshes"
```

---

### Task 12: Use stored per-account fingerprints in request headers

**Risk:** MODERATE. Touches request hook and account persistence.

**Objective:** Stop generating an unrelated random fingerprint for every request; use and persist the selected account's fingerprint.

**Files:**
- Modify: `antigravity_auth/interceptor.py:408-416`
- Modify: `antigravity_auth/fingerprint.py:96-106` if needed
- Modify: `antigravity_auth/test_interceptor.py`

**Step 1: Write failing test**

Add to `antigravity_auth/test_interceptor.py`:

```python
    def test_request_hook_uses_selected_account_fingerprint(self):
        from antigravity_auth.interceptor import _antigravity_request_hook

        class FakeAccount:
            index = 0
            fingerprint = {
                "userAgent": "UA/account-0",
                "clientMetadata": {"ideType": "ANTIGRAVITY", "platform": "MACOS", "pluginType": "GEMINI"},
            }

        config = type("Config", (), {
            "cli_first": False,
            "soft_quota_cache_ttl_minutes": "auto",
            "quota_refresh_interval_minutes": 15,
            "account_selection_strategy": "sticky",
            "pid_offset_enabled": False,
            "soft_quota_threshold_percent": 100,
        })()
        request = httpx.Request(
            "POST",
            "https://cloudcode-pa.googleapis.com/v1internal:generateContent",
            headers={"Authorization": "Bearer stale", "Content-Type": "application/json"},
            json={"model": "claude-sonnet-4-6", "request": {"contents": []}},
        )

        with patch("antigravity_auth.config.get_config", return_value=config), \
             patch("antigravity_auth.interceptor._select_request_account", return_value={"access": "a", "account_index": 0, "account": FakeAccount()}):
            _antigravity_request_hook(request)

        self.assertEqual(request.headers["User-Agent"], "UA/account-0")
        self.assertIn('"platform": "MACOS"', request.headers["Client-Metadata"])
```

**Step 2: Verify RED**

Run:

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -k selected_account_fingerprint -v
```

Expected: FAIL because request hook currently generates a fresh fingerprint and only applies `Client-Metadata`.

**Step 3: Implement selected-account fingerprint usage**

Replace the random `generate_fingerprint()` block with:

```python
    try:
        from .fingerprint import build_fingerprint_headers, generate_fingerprint, update_fingerprint_version
        account = selected.get("account") if selected else None
        if account is not None:
            if not getattr(account, "fingerprint", None):
                account.fingerprint = generate_fingerprint()
            changed = update_fingerprint_version(account.fingerprint)
            for key, val in build_fingerprint_headers(account.fingerprint).items():
                request.headers[key] = val
            cm = account.fingerprint.get("clientMetadata")
            if cm:
                request.headers["Client-Metadata"] = json.dumps(cm)
            if changed:
                try:
                    from .accounts.shared import get_or_create_global_manager
                    get_or_create_global_manager().save_to_disk()
                except Exception:
                    pass
    except Exception:
        pass
```

Keep `build_antigravity_headers()` as fallback when no selected account exists.

**Step 4: Verify GREEN and import chain**

Run:

```bash
python3 -m pytest antigravity_auth/test_interceptor.py -k "selected_account_fingerprint or request_hook" -v
python3 -m pytest antigravity_auth/test_fingerprint.py antigravity_auth/test_interceptor.py -q
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.interceptor import install; print('interceptor OK')"
python3 -c "from antigravity_auth.fingerprint import build_fingerprint_headers; print('fingerprint OK')"
```

Expected: tests pass; probes print OK.

**Step 5: Commit**

```bash
git add antigravity_auth/interceptor.py antigravity_auth/test_interceptor.py
git commit -m "fix: reuse per-account antigravity fingerprints"
```

---

## Phase 3 — Integration, Docs, and Deferred-Complexity Decisions

### Task 13: Make provider wrapper install the interceptor idempotently

**Risk:** HIGH. Touches plugin side-effect loading.

**Objective:** Ensure provider alias registration cannot happen without runtime interception when the model-provider wrapper loads.

**Files:**
- Modify: `antigravity_auth/hermes_provider_plugin.py:138-159`
- Modify: `antigravity_auth/test_hermes_plugin.py`
- Do not modify: `plugins/model-providers/antigravity/__init__.py` import-star pattern except if tests require comments.

**Step 1: Write failing test**

Add test using mocks so Hermes internals are not required:

```python
  def test_provider_plugin_installs_interceptor_best_effort(self):
      import importlib
      import sys
      import types
      from unittest.mock import Mock, patch

      class FakeProviderProfile:
          def __init__(self, **kwargs):
              self.__dict__.update(kwargs)

      providers = types.ModuleType("providers")
      providers.register_provider = Mock()
      providers_base = types.ModuleType("providers.base")
      providers_base.ProviderProfile = FakeProviderProfile

      with patch.dict(sys.modules, {
          "providers": providers,
          "providers.base": providers_base,
      }), patch("antigravity_auth.interceptor.install", return_value=True) as install:
          sys.modules.pop("antigravity_auth.hermes_provider_plugin", None)
          importlib.import_module("antigravity_auth.hermes_provider_plugin")

      install.assert_called_once()
```

**Step 2: Verify RED**

Run:

```bash
python3 -m pytest antigravity_auth/test_hermes_plugin.py -k provider_plugin_installs_interceptor -v
```

Expected: FAIL because provider module does not install interceptor.

**Step 3: Implement best-effort install without breaking side effects**

At the bottom of `hermes_provider_plugin.py`, after provider registration/picker patch, add:

```python
try:
  from .interceptor import install as _install_interceptor
  _install_interceptor()
except Exception:
  pass
```

Do not wrap or suppress `register_provider(antigravity)` itself in a way that hides provider registration failures beyond current behavior.

**Step 4: Verify GREEN and side-effect imports**

Run:

```bash
python3 -m pytest antigravity_auth/test_hermes_plugin.py -k "provider_plugin_installs_interceptor or register" -v
python3 -m pytest antigravity_auth/ -q
python3 - <<'PY'
import sys, types
class FakeProviderProfile:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
providers = types.ModuleType("providers")
providers.register_provider = lambda profile: None
providers_base = types.ModuleType("providers.base")
providers_base.ProviderProfile = FakeProviderProfile
sys.modules["providers"] = providers
sys.modules["providers.base"] = providers_base
from antigravity_auth.hermes_provider_plugin import antigravity
print("provider OK")
PY
python3 -c "from antigravity_auth.interceptor import install; print('interceptor OK')"
```

Expected: tests pass; imports print OK.

**Step 5: Commit**

```bash
git add antigravity_auth/hermes_provider_plugin.py antigravity_auth/test_hermes_plugin.py
git commit -m "fix: install interceptor from provider plugin"
```

---

### Task 14: Decide and codify SSE response behavior

**Risk:** MODERATE. Utility transform path; not currently the Hermes Cloud Code heartbeat according to architecture docs.

**Objective:** Remove contradiction between `transform/AGENTS.md`/docs and implementation. Either implement OpenAI chunk conversion or document/test usage-only SSE passthrough.

**Files:**
- Modify: `antigravity_auth/transform/response.py:293-295`
- Modify: `antigravity_auth/transform/test_response.py`
- Modify: `antigravity_auth/transform/AGENTS.md:13-24`
- Modify: `docs/ARCHITECTURE.md:197-204`

**Step 1: Pick the intended behavior**

Default recommendation for this project: keep passthrough because `docs/ARCHITECTURE.md` says this module is utility/test coverage and current runtime uses native Hermes Cloud Code response parsing. Rename the contract to "usage extraction + error adaptation" rather than building a partial OpenAI stream converter.

**Step 2: Write docs/test-only assertion**

Update `transform/AGENTS.md` line that says SSE conversion to:

```markdown
├── response.py    # Response utility: usage extraction, error rewrites, non-stream response unwrap; streaming SSE is passed through
```

Add/adjust test name in `test_response.py`:

```python
def test_sse_passthrough_is_documented_contract(self):
    body = 'data: {"response":{"usageMetadata":{"totalTokenCount":1}}}\n\n'
    transformed, headers, extra = transform_antigravity_response(
        body,
        streaming=True,
        status_code=200,
        headers={"Content-Type": "text/event-stream"},
    )
    self.assertEqual(transformed, body)
    self.assertIsNotNone(headers)
    self.assertIsNone(extra)
```

**Step 3: Run tests**

```bash
python3 -m pytest antigravity_auth/transform/test_response.py -k sse -v
python3 -m pytest antigravity_auth/transform/ -q
```

Expected: pass.

**Step 4: Commit**

```bash
git add antigravity_auth/transform/response.py antigravity_auth/transform/test_response.py antigravity_auth/transform/AGENTS.md docs/ARCHITECTURE.md
git commit -m "docs: codify antigravity sse passthrough behavior"
```

If future work requires true OpenAI streaming chunks, create a separate plan; do not hide that larger change inside this remediation batch.

---

### Task 15: Fix nullable schema required-field semantics

**Risk:** MODERATE. Utility transform path.

**Objective:** Stop detecting nullability via description substring and stop changing required-key presence solely because a value may be null.

**Files:**
- Modify: `antigravity_auth/transform/schema.py:115-120`, `antigravity_auth/transform/schema.py:461-482`
- Modify: `antigravity_auth/transform/test_schema.py`

**Step 1: Write failing tests**

Add to `test_schema.py`:

```python
  def test_non_nullable_description_does_not_remove_required(self):
    schema = {
      "type": "object",
      "properties": {"id": {"type": "string", "description": "non-nullable identifier"}},
      "required": ["id"],
    }
    cleaned = clean_json_schema(schema)
    self.assertEqual(cleaned.get("required"), ["id"])

  def test_nullable_type_array_preserves_required_presence(self):
    schema = {
      "type": "object",
      "properties": {"id": {"type": ["string", "null"]}},
      "required": ["id"],
    }
    cleaned = clean_json_schema(schema)
    self.assertEqual(cleaned.get("required"), ["id"])
    self.assertIn("nullable", cleaned["properties"]["id"].get("description", ""))
```

**Step 2: Verify RED**

Run:

```bash
python3 -m pytest antigravity_auth/transform/test_schema.py -k "nullable_description or nullable_type_array_preserves" -v
```

Expected: FAIL because current code removes required on nullable substring.

**Step 3: Implement minimal semantic fix**

Remove `_schema_is_nullable()` if no longer used. In `_flatten_type_arrays()`, keep appending the `nullable` description hint but delete the block that accumulates `nullable_fields` and removes them from `required`:

```python
  # Recursively process properties
  if isinstance(result.get("properties"), dict):
    new_props = {}
    for prop_key, prop_value in result["properties"].items():
      new_props[prop_key] = _flatten_type_arrays(prop_value)
    result["properties"] = new_props
```

Do not remove entries from `required` except in `_cleanup_required_fields()` when the property truly does not exist.

**Step 4: Update old tests**

Existing tests that expect nullable fields to be removed from `required` must be rewritten to assert the new behavior. Required means "key must be present" even if value may be null.

**Step 5: Verify GREEN**

Run:

```bash
python3 -m pytest antigravity_auth/transform/test_schema.py -v
python3 -m pytest antigravity_auth/transform/ -q
python3 -m pytest antigravity_auth/ -q
```

Expected: all pass.

**Step 6: Commit**

```bash
git add antigravity_auth/transform/schema.py antigravity_auth/transform/test_schema.py
git commit -m "fix: preserve required semantics for nullable schemas"
```

---

### Task 16: Add process locks around auth/account store writes

**Risk:** HIGH. Touches persistent credential state.

**Objective:** Serialize atomic account-store writes and auth.json read-modify-write writes across Hermes processes without introducing lock reentrancy deadlocks.

**Non-goal:** This task does not claim full stale-load/lost-update prevention for every existing `load_accounts() -> mutate -> save_accounts()` account-store call site. Migrating those broader CLI/token/interceptor call sites to a dedicated account-store mutation helper should be a separate follow-up task if strict RMW isolation is required.

**Files:**
- Modify: `antigravity_auth/storage.py:18-23`, `antigravity_auth/storage.py:174-189`, `antigravity_auth/storage.py:221-260`
- Modify: `antigravity_auth/test_storage.py`

**Step 1: Write unit tests for lock helper**

Add a small helper class test instead of trying to race processes in unit tests:

```python
    def test_process_lock_creates_private_lock_file_and_can_reacquire(self):
        import os
        import stat
        import tempfile
        from pathlib import Path
        from antigravity_auth.storage import _process_file_lock

        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "store.lock"
            with _process_file_lock(lock_path):
                self.assertTrue(lock_path.exists())
                self.assertEqual(stat.S_IMODE(os.stat(lock_path).st_mode), 0o600)
            with _process_file_lock(lock_path):
                self.assertTrue(lock_path.exists())
                self.assertEqual(stat.S_IMODE(os.stat(lock_path).st_mode), 0o600)
```

**Step 2: Verify RED**

Run:

```bash
python3 -m pytest antigravity_auth/test_storage.py -k process_lock -v
```

Expected: FAIL because `_process_file_lock` does not exist.

**Step 3: Implement lock context manager**

In `storage.py` imports add `contextlib`. Then add near `_secret_file_opener`:

```python
import contextlib


def _lock_file_opener(path: str, flags: int) -> int:
    return os.open(path, flags | os.O_CREAT, 0o600)


@contextlib.contextmanager
def _process_file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8", opener=_lock_file_opener) as lock_file:
        try:
            os.chmod(lock_path, 0o600)
        except Exception:
            pass
        try:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
```

Refactor account reads/writes into unlocked helpers plus public locking wrappers. Keep the normalization behavior identical, but move it behind an unlocked helper so callers that already hold `_accounts_store_lock` do not deadlock:

```python
def _default_accounts_storage() -> dict[str, Any]:
    return {
        "version": 4,
        "accounts": [],
        "activeIndex": 0,
        "cursor": 0,
        "activeIndexByFamily": {
            "claude": 0,
            "gemini": 0,
        },
    }


def _load_accounts_unlocked(path: Path | None = None) -> dict[str, Any]:
    default_storage = _default_accounts_storage()
    path = path or get_accounts_json_path()
    if not path.exists():
        return default_storage
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default_storage
        if "version" not in data:
            data["version"] = 4
        if "accounts" not in data or not isinstance(data["accounts"], list):
            data["accounts"] = []
        if "activeIndex" not in data:
            data["activeIndex"] = 0
        if "cursor" not in data:
            data["cursor"] = data["activeIndex"]
        if "activeIndexByFamily" not in data or not isinstance(data["activeIndexByFamily"], dict):
            data["activeIndexByFamily"] = {
                "claude": 0,
                "gemini": 0,
            }
        else:
            family = data["activeIndexByFamily"]
            if "claude" not in family:
                family["claude"] = 0
            if "gemini" not in family:
                family["gemini"] = 0
        return data
    except Exception:
        return default_storage


def load_accounts() -> dict[str, Any]:
    path = get_accounts_json_path()
    with _accounts_store_lock:
        return _load_accounts_unlocked(path)
```

Then move account writes into an unlocked implementation plus the public locking wrapper:

```python
def _save_accounts_unlocked(path: Path, storage_dict: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f".json.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8", opener=_secret_file_opener) as f:
            json.dump(storage_dict, f, indent=2)
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        raise


def save_accounts(storage_dict: dict[str, Any]) -> None:
    path = get_accounts_json_path()
    with _process_file_lock(path.with_suffix(".lock")), _accounts_store_lock:
        _save_accounts_unlocked(path, storage_dict)
```

If this task changes or adds any account-store helper that performs read-modify-write, it must hold the process lock for the full read/mutate/write sequence and call `_save_accounts_unlocked(path, data)` instead of `save_accounts(data)` while the lock is already held:

```python
    path = get_accounts_json_path()
    with _process_file_lock(path.with_suffix(".lock")), _accounts_store_lock:
        data = _load_accounts_unlocked(path)
        # Perform the account mutation inside this locked block.
        _save_accounts_unlocked(path, data)
```

Do not call public `load_accounts()` or locking `save_accounts()` from inside an already-held account-store process/thread lock; use `_load_accounts_unlocked(path)` and `_save_accounts_unlocked(path, data)` there. Do not add process locks around network calls or long-running API probes.

Wrap `sync_token_to_auth_json()` full read-modify-write body:

```python
    with _process_file_lock(path.with_suffix(".lock")), _auth_store_lock:
        # Move the current sync_token_to_auth_json() data initialization,
        # existing-file read, provider update, and atomic temp-file write here.
```

Do not hold the process lock around unrelated network calls.

**Step 4: Verify GREEN**

Run:

```bash
python3 -m pytest antigravity_auth/test_storage.py -v
python3 -m pytest antigravity_auth/ -q
python3 -c "from antigravity_auth.storage import save_accounts, sync_token_to_auth_json; print('storage OK')"
```

Expected: tests pass; import prints OK.

**Step 5: Commit**

```bash
git add antigravity_auth/storage.py antigravity_auth/test_storage.py
git commit -m "fix: lock antigravity auth store writes across processes"
```

---

### Task 17: Final docs, package, import-chain, and runtime smoke audit

**Risk:** MODERATE. No production logic unless fixing discovered documentation drift.

**Objective:** Verify the whole remediation is internally consistent and ready to ship.

**Files:**
- Modify only if verification exposes drift: `README.md`, `docs/ARCHITECTURE.md`, `docs/ANTIGRAVITY_API_SPEC.md`, `CHANGELOG.md`

**Step 1: Run full local gates**

```bash
python3 -m pytest antigravity_auth/ -q
python3 -m compileall -q antigravity_auth plugins
python3 -c "from antigravity_auth.interceptor import install; print('interceptor OK')"
python3 -c "from antigravity_auth.accounts.manager import AccountManager; print('manager OK')"
python3 -c "from antigravity_auth.accounts.ratelimit import mark_rate_limited, mark_rate_limited_with_reason; print('ratelimit OK')"
python3 - <<'PY'
import sys, types
class FakeProviderProfile:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
providers = types.ModuleType("providers")
providers.register_provider = lambda profile: None
providers_base = types.ModuleType("providers.base")
providers_base.ProviderProfile = FakeProviderProfile
sys.modules["providers"] = providers
sys.modules["providers.base"] = providers_base
from antigravity_auth.hermes_provider_plugin import antigravity
print("provider OK")
PY
```

Expected: tests pass, compileall returns no output, probes print OK.

**Step 2: Run packaging artifact check in a clean tree**

Do not run this against a tree containing local ignored `antigravity_auth/_credentials.py` unless the expected result is a deliberate build refusal.

```bash
tmp=$(mktemp -d)
git archive --format=tar HEAD | tar -x -C "$tmp" -f -
cd "$tmp"
python3 -m pip install build
python3 -m build --sdist --wheel
python3 - <<'PY'
from pathlib import Path
import tarfile
import zipfile
for artifact in Path('dist').iterdir():
    if artifact.suffix == '.whl':
        with zipfile.ZipFile(artifact) as z:
            names = z.namelist()
    else:
        with tarfile.open(artifact) as t:
            names = [m.name for m in t.getmembers()]
    assert not any(name.endswith('antigravity_auth/_credentials.py') for name in names), artifact
print('artifact credential check OK')
PY
```

Expected: `artifact credential check OK`.

**Step 3: Smoke-test plugin wrapper generation**

```bash
tmp_home=$(mktemp -d)
HERMES_HOME="$tmp_home" python3 -m antigravity_auth.install_plugins
python3 - "$tmp_home" <<'PY'
from pathlib import Path
import sys
home = Path(sys.argv[1])
expected = [
  home / 'plugins' / 'antigravity-cli' / '__init__.py',
  home / 'plugins' / 'antigravity-cli' / 'plugin.yaml',
  home / 'plugins' / 'model-providers' / 'antigravity' / '__init__.py',
  home / 'plugins' / 'model-providers' / 'antigravity' / 'plugin.yaml',
]
missing = [str(path) for path in expected if not path.exists()]
if missing:
    raise SystemExit(f'missing generated plugin files: {missing}')
print('plugin wrapper generation OK')
PY
```

Expected: `plugin wrapper generation OK`.

**Step 4: Runtime routing smoke**

If valid credentials are available, run:

```bash
hermes -z "Say OK" --provider antigravity --model claude-sonnet-4-6
```

Acceptable outcomes:
- Live response succeeds; or
- Clear Cloud Code / `cloudcode-pa` auth/quota/model error proving routing reached the intended backend.

Unacceptable outcome:
- Any OpenRouter fallback or provider-not-found route.

If credentials are not available, run import/provider smoke only and state that live routing was not verified.

**Step 5: Post-implementation audit checklist**

Read every changed file and verify:

- No request hook body mutation was added.
- No wildcard provider-wrapper import was replaced.
- No duplicate token refresh/API calls were introduced in CLI quota/check paths.
- All touched heartbeat files have import probes.
- `Authorization` handling fails closed only on account-selection failure; selected tokens still override correctly.
- 403/429 acts on request-selected account, not current account.
- Credential docs and code agree.
- Package artifacts exclude local credentials.
- Storage report is precise: Task 16 serializes writes/auth.json RMW only; do not claim full account-store stale-load/lost-update prevention unless a separate mutation-helper migration was completed.

**Step 6: Commit docs/changelog if needed**

```bash
git add README.md docs/ARCHITECTURE.md docs/ANTIGRAVITY_API_SPEC.md CHANGELOG.md
git commit -m "docs: record antigravity remediation verification"
```

Skip commit if no docs changed.

---

## Final Verification Summary Required Before Reporting Done

The implementer must paste this checklist into the final report with actual command results:

```text
[ ] python3 -m pytest antigravity_auth/ -q -> PASS
[ ] python3 -m compileall -q antigravity_auth plugins -> PASS
[ ] import-chain probes -> PASS
[ ] clean-tree artifact credential check -> PASS
[ ] temp HERMES_HOME plugin wrapper generation -> PASS
[ ] docs credential/redirect references -> PASS
[ ] runtime smoke classification: live success OR cloudcode-pa error OR not run due missing credentials
[ ] git status --short -> clean except allowed ignored local files
```

Do not claim runtime/live provider success unless the actual smoke command ran and the output proves Cloud Code routing or live success.
