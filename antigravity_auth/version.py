"""Version check — compares installed version against latest GitHub release."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

from .package_info import CANONICAL_INSTALL_COMMAND, __version__

GITHUB_API_URL = "https://api.github.com/repos/Reedtrullz/hermes-antigravity-auth/releases/latest"
CHECK_INTERVAL_SECONDS = 86400  # 24 hours
REQUEST_TIMEOUT_SECONDS = 5

_version_thread: threading.Thread | None = None


def _get_installed_version() -> str:
    """Read version from the package's single source of truth."""
    return __version__


def _parse_github_tag(tag: str) -> str:
    """Strip leading 'v' from tag names like 'v1.7.0'."""
    return tag.lstrip("v")


def _get_cache_path() -> Path:
    from antigravity_auth.storage import get_hermes_home
    return get_hermes_home() / "antigravity-version-check.json"


def _is_cache_fresh() -> bool:
    cache_path = _get_cache_path()
    if not cache_path.exists():
        return False
    try:
        with open(cache_path) as f:
            data = json.load(f)
        last_check = data.get("last_check", 0)
        return (time.time() - last_check) < CHECK_INTERVAL_SECONDS
    except Exception:
        return False


def _write_cache() -> None:
    cache_path = _get_cache_path()
    tmp_path = cache_path.with_suffix(f".json.{os.getpid()}.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump({"last_check": time.time()}, f)
        os.replace(tmp_path, cache_path)
    except Exception:
        pass


def _check_version() -> None:
    """Compare installed version against latest GitHub release."""
    if _is_cache_fresh():
        return

    if os.environ.get("HERMES_ANTIGRAVITY_VERSION_CHECK", "1") != "1":
        # Opt-in flag disabled — skip the version check
        import logging
        logging.getLogger(__name__).info("Version check disabled via HERMES_ANTIGRAVITY_VERSION_CHECK")
        return

    installed = _get_installed_version()

    try:
        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "hermes-antigravity-auth-version-check"},
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        latest = _parse_github_tag(data.get("tag_name", "0.0.0"))
    except Exception:
        _write_cache()
        return

    _write_cache()

    if _version_newer(latest, installed):
        _notify_update(installed, latest)


def _version_newer(latest: str, installed: str) -> bool:
    """Compare two semver strings. Returns True if latest > installed."""
    try:
        latest_parts = [int(x) for x in latest.split(".")]
        installed_parts = [int(x) for x in installed.split(".")]
        while len(latest_parts) < 3:
            latest_parts.append(0)
        while len(installed_parts) < 3:
            installed_parts.append(0)
        return latest_parts > installed_parts
    except (ValueError, AttributeError):
        return latest != installed


def _notify_update(installed: str, latest: str) -> None:
    """Print update notification to stderr."""
    print(
        f"\n[antigravity] Update available: v{installed} → v{latest}\n"
        f"  Run: {CANONICAL_INSTALL_COMMAND}\n",
        file=sys.stderr,
        flush=True,
    )


def start_version_check() -> None:
    """Start a daemon thread that checks for plugin updates. Idempotent."""
    global _version_thread
    if _version_thread is not None and _version_thread.is_alive():
        return
    _version_thread = threading.Thread(
        target=_check_version, daemon=True, name="antigravity-version-check"
    )
    _version_thread.start()
