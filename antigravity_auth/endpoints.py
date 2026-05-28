"""Antigravity API endpoint helpers.

select_endpoint() currently pins production. EndpointProvider still records failed
endpoints for diagnostics, but failed-endpoint tracking is a no-op for selection
until the fallback chain is re-enabled.
"""
from __future__ import annotations

import time
from typing import Any

from .constants import ANTIGRAVITY_ENDPOINT_FALLBACKS, ANTIGRAVITY_ENDPOINT_PROD

_FAILURE_TTL_SECONDS = 300


class EndpointProvider:
  """Stores Antigravity API endpoint fallback-chain state.

  The provider can compute daily → autopush → prod order and record temporary
  endpoint failures, but module-level select_endpoint() currently pins prod so
  these failure marks are diagnostic/no-op for request routing until fallback is
  re-enabled.
  """

  def __init__(self) -> None:
    self._failed_endpoints: dict[str, float] = {}

  def get_endpoints(self, header_style: str = "antigravity") -> list[str]:
    """Return the list of endpoints to try, in fallback order.

    For ``gemini-cli`` header style, only the production endpoint
    is returned (sandbox endpoints are skipped).
    """
    if header_style == "gemini-cli":
      return [ANTIGRAVITY_ENDPOINT_PROD]
    return list(ANTIGRAVITY_ENDPOINT_FALLBACKS)

  def mark_failed(self, endpoint: str) -> None:
    """Record an endpoint failure for diagnostics/future fallback routing."""
    self._failed_endpoints[endpoint] = time.time()

  def is_failed(self, endpoint: str) -> bool:
    """Check whether an endpoint has been marked as failed (with TTL expiry)."""
    failure_time = self._failed_endpoints.get(endpoint)
    if failure_time is None:
      return False
    if time.time() - failure_time > _FAILURE_TTL_SECONDS:
      self._failed_endpoints.pop(endpoint, None)
      return False
    return True

  def reset(self) -> None:
    """Clear all endpoint failure marks."""
    self._failed_endpoints.clear()

  @property
  def failed_endpoints(self) -> set[str]:
    """Return currently failed endpoints (expired entries are cleaned)."""
    now = time.time()
    expired = [ep for ep, ts in self._failed_endpoints.items() if now - ts > _FAILURE_TTL_SECONDS]
    for ep in expired:
      self._failed_endpoints.pop(ep, None)
    return set(self._failed_endpoints.keys())



# Module-level endpoint provider — shared across all requests
_endpoint_provider = EndpointProvider()


def select_endpoint(config=None):
    """Return the production Antigravity endpoint.

    The fallback chain (daily → autopush → prod) is currently disabled because
    daily sandbox rejects free-tier accounts for Claude. Failed-endpoint tracking
    remains diagnostic only and does not affect selection until fallback is
    re-enabled.

    Args:
        config: Optional Config dataclass instance (currently unused).
    """
    from .constants import ANTIGRAVITY_ENDPOINT_PROD

    # Use PROD by default — daily sandbox rejects free-tier accounts for Claude
    return ANTIGRAVITY_ENDPOINT_PROD


def mark_endpoint_failed(endpoint: str) -> None:
    """Record endpoint failure diagnostics (selection currently ignores this)."""
    _endpoint_provider.mark_failed(endpoint)


def reset_endpoint_failures() -> None:
    """Clear all endpoint failure marks (e.g., after a period of stability)."""
    _endpoint_provider.reset()
