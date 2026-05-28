import time
import unittest
from unittest.mock import patch

from antigravity_auth.accounts.ratelimit import (
    get_quota_key,
    parse_rate_limit_reason,
    calculate_backoff_ms,
    clear_expired_rate_limits,
    is_rate_limited_for_quota_key,
    is_rate_limited_for_family,
    is_rate_limited_for_header_style,
    is_account_cooling_down,
    mark_rate_limited,
    mark_rate_limited_with_reason,
    RateLimitTracker,
)
from antigravity_auth.accounts.state import (
    ManagedAccount,
    RefreshParts,
    RateLimitState,
    RATE_LIMIT_REASON_QUOTA_EXHAUSTED,
    RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED,
    RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED,
    RATE_LIMIT_REASON_SERVER_ERROR,
    RATE_LIMIT_REASON_UNKNOWN,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_account(
    index: int = 0,
    email: str | None = "test@example.com",
) -> ManagedAccount:
    """Create a minimal ManagedAccount for testing."""
    return ManagedAccount(
        index=index,
        refresh_parts=RefreshParts(refresh_token="fake-token"),
        email=email,
    )


# ---------------------------------------------------------------------------
# TestGetQuotaKey
# ---------------------------------------------------------------------------

class TestGetQuotaKey(unittest.TestCase):
    """Tests for get_quota_key()."""

    def test_claude_family_returns_claude(self):
        """Claude family always returns 'claude' regardless of header style."""
        self.assertEqual(get_quota_key("claude", "antigravity"), "claude")
        self.assertEqual(get_quota_key("claude", "gemini-cli"), "claude")
        self.assertEqual(get_quota_key("claude", "antigravity", "claude-3-opus"), "claude")

    def test_gemini_antigravity_returns_gemini_antigravity(self):
        """Gemini + antigravity header style returns 'gemini-antigravity'."""
        self.assertEqual(get_quota_key("gemini", "antigravity"), "gemini-antigravity")

    def test_gemini_cli_returns_gemini_cli(self):
        """Gemini + gemini-cli header style returns 'gemini-cli'."""
        self.assertEqual(get_quota_key("gemini", "gemini-cli"), "gemini-cli")

    def test_model_specific_key_for_antigravity(self):
        """Model-specific key for antigravity: 'gemini-antigravity:model'."""
        self.assertEqual(
            get_quota_key("gemini", "antigravity", "gemini-2.5-pro"),
            "gemini-antigravity:gemini-2.5-pro",
        )

    def test_model_specific_key_for_cli(self):
        """Model-specific key for gemini-cli: 'gemini-cli:model'."""
        self.assertEqual(
            get_quota_key("gemini", "gemini-cli", "gemini-3-flash-preview"),
            "gemini-cli:gemini-3-flash-preview",
        )

    def test_model_none_returns_base_key(self):
        """When model is None, base key is returned (not 'base:None')."""
        self.assertEqual(get_quota_key("gemini", "antigravity", None), "gemini-antigravity")
        self.assertEqual(get_quota_key("gemini", "gemini-cli", None), "gemini-cli")


# ---------------------------------------------------------------------------
# TestParseRateLimitReason
# ---------------------------------------------------------------------------

class TestParseRateLimitReason(unittest.TestCase):
    """Tests for parse_rate_limit_reason()."""

    # -- Status code tests --

    def test_529_returns_capacity_exhausted(self):
        """HTTP 529 → MODEL_CAPACITY_EXHAUSTED."""
        self.assertEqual(
            parse_rate_limit_reason(None, None, 529),
            RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED,
        )

    def test_503_returns_capacity_exhausted(self):
        """HTTP 503 → MODEL_CAPACITY_EXHAUSTED."""
        self.assertEqual(
            parse_rate_limit_reason(None, None, 503),
            RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED,
        )

    def test_500_returns_server_error(self):
        """HTTP 500 → SERVER_ERROR."""
        self.assertEqual(
            parse_rate_limit_reason(None, None, 500),
            RATE_LIMIT_REASON_SERVER_ERROR,
        )

    # -- Explicit reason string tests --

    def test_explicit_quota_exhausted(self):
        """Explicit reason 'QUOTA_EXHAUSTED' is matched (case-insensitive)."""
        self.assertEqual(
            parse_rate_limit_reason("QUOTA_EXHAUSTED", None, None),
            RATE_LIMIT_REASON_QUOTA_EXHAUSTED,
        )
        self.assertEqual(
            parse_rate_limit_reason("quota_exhausted", None, None),
            RATE_LIMIT_REASON_QUOTA_EXHAUSTED,
        )

    def test_explicit_rate_limit_exceeded(self):
        """Explicit reason 'RATE_LIMIT_EXCEEDED' is matched."""
        self.assertEqual(
            parse_rate_limit_reason("RATE_LIMIT_EXCEEDED", None, None),
            RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED,
        )
        self.assertEqual(
            parse_rate_limit_reason("rate_limit_exceeded", None, None),
            RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED,
        )

    def test_explicit_model_capacity_exhausted(self):
        """Explicit reason 'MODEL_CAPACITY_EXHAUSTED' is matched."""
        self.assertEqual(
            parse_rate_limit_reason("MODEL_CAPACITY_EXHAUSTED", None, None),
            RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED,
        )

    # -- Message text scanning tests --

    def test_message_overloaded_returns_capacity(self):
        """Message containing 'overloaded' → MODEL_CAPACITY_EXHAUSTED."""
        self.assertEqual(
            parse_rate_limit_reason(None, "The model is overloaded", None),
            RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED,
        )

    def test_message_capacity_returns_capacity(self):
        """Message containing 'capacity' → MODEL_CAPACITY_EXHAUSTED."""
        self.assertEqual(
            parse_rate_limit_reason(None, "Model capacity exhausted", None),
            RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED,
        )

    def test_message_resource_exhausted_returns_capacity(self):
        """Message containing 'resource exhausted' → MODEL_CAPACITY_EXHAUSTED."""
        self.assertEqual(
            parse_rate_limit_reason(None, "Resource exhausted. Please try later.", None),
            RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED,
        )

    def test_message_per_minute_returns_rpm(self):
        """Message containing 'per minute' → RATE_LIMIT_EXCEEDED."""
        self.assertEqual(
            parse_rate_limit_reason(None, "429 requests per minute exceeded", None),
            RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED,
        )

    def test_message_rate_limit_returns_rpm(self):
        """Message containing 'rate limit' → RATE_LIMIT_EXCEEDED."""
        self.assertEqual(
            parse_rate_limit_reason(None, "You have hit a rate limit", None),
            RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED,
        )

    def test_message_too_many_requests_returns_rpm(self):
        """Message containing 'too many requests' → RATE_LIMIT_EXCEEDED."""
        self.assertEqual(
            parse_rate_limit_reason(None, "Too many requests", None),
            RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED,
        )

    def test_message_quota_returns_quota_exhausted(self):
        """Message containing 'quota' (but no capacity/overloaded) → QUOTA_EXHAUSTED."""
        self.assertEqual(
            parse_rate_limit_reason(None, "Your quota has been reached", None),
            RATE_LIMIT_REASON_QUOTA_EXHAUSTED,
        )

    def test_message_exhausted_returns_quota_exhausted(self):
        """Message containing 'exhausted' (but no capacity/overloaded/rpm) → QUOTA_EXHAUSTED."""
        self.assertEqual(
            parse_rate_limit_reason(None, "Quota exhausted for today", None),
            RATE_LIMIT_REASON_QUOTA_EXHAUSTED,
        )

    def test_capacity_takes_priority_over_rpm(self):
        """Capacity/overloaded takes priority over 'per minute' matching."""
        self.assertEqual(
            parse_rate_limit_reason(None, "overloaded rate limit per minute", None),
            RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED,
        )

    def test_429_returns_unknown_when_no_match(self):
        """HTTP 429 with no matching reason/message → UNKNOWN."""
        self.assertEqual(
            parse_rate_limit_reason(None, "Some unrecognized error", 429),
            RATE_LIMIT_REASON_UNKNOWN,
        )

    def test_no_status_no_reason_no_message_returns_unknown(self):
        """No status, reason, or message → UNKNOWN."""
        self.assertEqual(
            parse_rate_limit_reason(None, None, None),
            RATE_LIMIT_REASON_UNKNOWN,
        )

    def test_status_overrides_message(self):
        """Status code is checked before message scanning."""
        self.assertEqual(
            parse_rate_limit_reason(None, "quota exhausted per minute", 503),
            RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED,
        )


# ---------------------------------------------------------------------------
# TestCalculateBackoff
# ---------------------------------------------------------------------------

class TestCalculateBackoff(unittest.TestCase):
    """Tests for calculate_backoff_ms()."""

    def test_retry_after_explicit(self):
        """Explicit retry_after_ms is used directly (capped at MIN_BACKOFF_MS)."""
        self.assertEqual(calculate_backoff_ms("anything", retry_after_ms=5000), 5000)

    def test_retry_after_below_min_backoff(self):
        """retry_after_ms below MIN_BACKOFF_MS is clamped to MIN_BACKOFF_MS."""
        # Positive but small retry_after is clamped to MIN_BACKOFF_MS
        self.assertEqual(calculate_backoff_ms("anything", retry_after_ms=500), 2000)
        # Zero is falsy in Python, so it doesn't enter the retry_after branch
        self.assertEqual(calculate_backoff_ms("anything", retry_after_ms=0.0), 60_000)

    def test_quota_exhausted_tier0(self):
        """QUOTA_EXHAUSTED with 0 consecutive failures → 60000ms."""
        self.assertEqual(
            calculate_backoff_ms(RATE_LIMIT_REASON_QUOTA_EXHAUSTED, consecutive_failures=0),
            60_000,
        )

    def test_quota_exhausted_tier1(self):
        """QUOTA_EXHAUSTED with 1 consecutive failure → 300000ms."""
        self.assertEqual(
            calculate_backoff_ms(RATE_LIMIT_REASON_QUOTA_EXHAUSTED, consecutive_failures=1),
            300_000,
        )

    def test_quota_exhausted_tier2(self):
        """QUOTA_EXHAUSTED with 2 consecutive failures → 1800000ms."""
        self.assertEqual(
            calculate_backoff_ms(RATE_LIMIT_REASON_QUOTA_EXHAUSTED, consecutive_failures=2),
            1_800_000,
        )

    def test_quota_exhausted_tier3(self):
        """QUOTA_EXHAUSTED with 3 consecutive failures → 7200000ms."""
        self.assertEqual(
            calculate_backoff_ms(RATE_LIMIT_REASON_QUOTA_EXHAUSTED, consecutive_failures=3),
            7_200_000,
        )

    def test_quota_exhausted_clamped_to_max_tier(self):
        """QUOTA_EXHAUSTED with failures beyond array length clamps to last tier."""
        self.assertEqual(
            calculate_backoff_ms(RATE_LIMIT_REASON_QUOTA_EXHAUSTED, consecutive_failures=99),
            7_200_000,
        )

    def test_rate_limit_exceeded(self):
        """RATE_LIMIT_EXCEEDED → 30000ms."""
        self.assertEqual(
            calculate_backoff_ms(RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED),
            30_000,
        )

    def test_model_capacity_exhausted_in_range(self):
        """MODEL_CAPACITY_EXHAUSTED returns value in [30000, 75000] range."""
        for _ in range(20):
            backoff = calculate_backoff_ms(RATE_LIMIT_REASON_MODEL_CAPACITY_EXHAUSTED)
            self.assertGreaterEqual(backoff, 30_000,
                                    f"backoff {backoff} below 30000")
            self.assertLessEqual(backoff, 75_000,
                                 f"backoff {backoff} above 75000")

    def test_server_error(self):
        """SERVER_ERROR → 20000ms."""
        self.assertEqual(
            calculate_backoff_ms(RATE_LIMIT_REASON_SERVER_ERROR),
            20_000,
        )

    def test_unknown_reason(self):
        """Unknown reason → 60000ms."""
        self.assertEqual(
            calculate_backoff_ms(RATE_LIMIT_REASON_UNKNOWN),
            60_000,
        )

    def test_retry_after_overrides_reason(self):
        """retry_after_ms overrides the reason-based backoff."""
        self.assertEqual(
            calculate_backoff_ms(
                RATE_LIMIT_REASON_QUOTA_EXHAUSTED,
                consecutive_failures=0,
                retry_after_ms=10_000,
            ),
            10_000,
        )


# ---------------------------------------------------------------------------
# TestRateLimitState
# ---------------------------------------------------------------------------

class TestRateLimitState(unittest.TestCase):
    """Tests for RateLimitState and rate-limit checking functions."""

    def test_empty_state_not_limited(self):
        """Empty state → not rate limited for any key."""
        state = RateLimitState()
        self.assertFalse(is_rate_limited_for_quota_key(state, "claude"))
        self.assertFalse(is_rate_limited_for_quota_key(state, "gemini-antigravity"))
        self.assertFalse(is_rate_limited_for_quota_key(state, "gemini-cli"))

    def test_future_reset_is_limited(self):
        """Future reset time → rate limited."""
        state = RateLimitState()
        future = time.time() * 1000 + 60_000  # 1 minute from now
        state.set("claude", future)
        self.assertTrue(is_rate_limited_for_quota_key(state, "claude"))

    def test_past_reset_not_limited(self):
        """Past reset time → not rate limited."""
        state = RateLimitState()
        past = time.time() * 1000 - 60_000  # 1 minute ago
        state.set("gemini-antigravity", past)
        self.assertFalse(is_rate_limited_for_quota_key(state, "gemini-antigravity"))

    def test_expired_cleared_by_clear_function(self):
        """clear_expired_rate_limits removes past entries."""
        state = RateLimitState()
        past = time.time() * 1000 - 60_000
        future = time.time() * 1000 + 60_000
        state.set("claude", past)
        state.set("gemini-cli", future)
        clear_expired_rate_limits(state)
        # claude was expired → removed
        self.assertIsNone(state.get("claude"))
        # gemini-cli still in future → kept
        self.assertIsNotNone(state.get("gemini-cli"))

    def test_family_claude_checks_single_key(self):
        """For claude family, is_rate_limited_for_family checks only 'claude' key."""
        state = RateLimitState()
        state.set("claude", time.time() * 1000 + 60_000)
        self.assertTrue(is_rate_limited_for_family(state, "claude"))

    def test_family_gemini_both_pools_limited(self):
        """Gemini family is rate limited only when BOTH pools are limited."""
        state = RateLimitState()
        future = time.time() * 1000 + 60_000
        state.set("gemini-antigravity", future)
        state.set("gemini-cli", future)
        self.assertTrue(is_rate_limited_for_family(state, "gemini"))

    def test_family_gemini_only_one_pool_limited_not_fully_limited(self):
        """Gemini family with only one pool limited → NOT fully rate limited."""
        state = RateLimitState()
        future = time.time() * 1000 + 60_000
        state.set("gemini-antigravity", future)
        # gemini-cli is NOT set
        self.assertFalse(is_rate_limited_for_family(state, "gemini"))

    def test_family_gemini_only_cli_pool_limited_not_fully_limited(self):
        """Gemini family with only gemini-cli pool limited → NOT fully rate limited."""
        state = RateLimitState()
        future = time.time() * 1000 + 60_000
        state.set("gemini-cli", future)
        self.assertFalse(is_rate_limited_for_family(state, "gemini"))

    def test_header_style_antigravity_for_gemini(self):
        """is_rate_limited_for_header_style checks antigravity pool for gemini."""
        state = RateLimitState()
        future = time.time() * 1000 + 60_000
        state.set("gemini-antigravity", future)
        self.assertTrue(
            is_rate_limited_for_header_style(state, "gemini", "antigravity")
        )

    def test_header_style_cli_for_gemini(self):
        """is_rate_limited_for_header_style checks gemini-cli pool for gemini."""
        state = RateLimitState()
        future = time.time() * 1000 + 60_000
        state.set("gemini-cli", future)
        self.assertTrue(
            is_rate_limited_for_header_style(state, "gemini", "gemini-cli")
        )

    def test_header_style_for_claude_checks_claude_key(self):
        """For claude, header style is ignored — checks 'claude' key."""
        state = RateLimitState()
        future = time.time() * 1000 + 60_000
        state.set("claude", future)
        self.assertTrue(
            is_rate_limited_for_header_style(state, "claude", "antigravity")
        )
        self.assertTrue(
            is_rate_limited_for_header_style(state, "claude", "gemini-cli")
        )

    def test_model_specific_key_checked_first(self):
        """Model-specific key overrides base key check in header_style."""
        state = RateLimitState()
        future = time.time() * 1000 + 60_000
        model_key = "gemini-antigravity:gemini-2.5-pro"
        state.set(model_key, future)
        self.assertTrue(
            is_rate_limited_for_header_style(state, "gemini", "antigravity", "gemini-2.5-pro")
        )

    def test_model_specific_falls_back_to_base_key(self):
        """When model key is not limited, base key is checked."""
        state = RateLimitState()
        future = time.time() * 1000 + 60_000
        state.set("gemini-antigravity", future)
        self.assertTrue(
            is_rate_limited_for_header_style(state, "gemini", "antigravity", "some-other-model")
        )

    def test_extras_key(self):
        """Extras dict keys work for RateLimitState get/set/delete."""
        state = RateLimitState()
        key = "gemini-antigravity:custom-model"
        state.set(key, time.time() * 1000 + 60_000)
        self.assertTrue(is_rate_limited_for_quota_key(state, key))
        state.delete(key)
        self.assertFalse(is_rate_limited_for_quota_key(state, key))


# ---------------------------------------------------------------------------
# TestMarking
# ---------------------------------------------------------------------------

class TestMarking(unittest.TestCase):
    """Tests for mark_rate_limited and mark_rate_limited_with_reason."""

    def setUp(self):
        """Create a fresh ManagedAccount for each test."""
        self.account = _make_account()

    def test_mark_rate_limited_sets_future_reset(self):
        """mark_rate_limited sets a future reset time on the account."""
        mark_rate_limited(self.account, 5000, "gemini", "antigravity")
        key = "gemini-antigravity"
        reset_time = self.account.rate_limit_reset_times.get(key)
        self.assertIsNotNone(reset_time)
        assert reset_time is not None
        now = time.time() * 1000
        self.assertGreater(reset_time, now)
        self.assertLess(reset_time, now + 6000)

    def test_mark_rate_limited_with_model(self):
        """mark_rate_limited with model sets model-specific key."""
        mark_rate_limited(self.account, 10_000, "gemini", "gemini-cli", "gemini-flash")
        model_key = "gemini-cli:gemini-flash"
        self.assertIsNotNone(self.account.rate_limit_reset_times.get(model_key))

    def test_mark_rate_limited_for_claude(self):
        """mark_rate_limited for claude sets 'claude' key."""
        mark_rate_limited(self.account, 15_000, "claude")
        self.assertIsNotNone(self.account.rate_limit_reset_times.get("claude"))

    def test_mark_with_reason_increments_failures(self):
        """mark_rate_limited_with_reason increments consecutive_failures."""
        self.assertEqual(self.account.consecutive_failures, 0)
        mark_rate_limited_with_reason(
            self.account, "gemini", "antigravity", None,
            RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED,
        )
        self.assertEqual(self.account.consecutive_failures, 1)

    def test_mark_with_reason_increments_multiple(self):
        """Multiple marks with reason accumulate failures."""
        for i in range(3):
            mark_rate_limited_with_reason(
                self.account, "gemini", "antigravity", None,
                RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED,
            )
            self.assertEqual(self.account.consecutive_failures, i + 1)

    def test_mark_with_reason_sets_last_failure_time(self):
        """mark_rate_limited_with_reason records last_failure_time."""
        self.assertIsNone(self.account.last_failure_time)
        mark_rate_limited_with_reason(
            self.account, "gemini", "antigravity", None,
            RATE_LIMIT_REASON_SERVER_ERROR,
        )
        self.assertIsNotNone(self.account.last_failure_time)

    def test_mark_with_reason_returns_backoff(self):
        """mark_rate_limited_with_reason returns the calculated backoff."""
        backoff = mark_rate_limited_with_reason(
            self.account, "gemini", "antigravity", None,
            RATE_LIMIT_REASON_RATE_LIMIT_EXCEEDED,
        )
        self.assertEqual(backoff, 30_000)

    def test_mark_with_reason_quota_tiered_backoff(self):
        """Quota exhausted backoff tiers with successive failures."""
        # First failure → tier 0 (60s)
        b1 = mark_rate_limited_with_reason(
            self.account, "gemini", "antigravity", None,
            RATE_LIMIT_REASON_QUOTA_EXHAUSTED,
        )
        self.assertEqual(b1, 60_000)

        # Second failure → tier 1 (300s)
        b2 = mark_rate_limited_with_reason(
            self.account, "gemini", "antigravity", None,
            RATE_LIMIT_REASON_QUOTA_EXHAUSTED,
        )
        self.assertEqual(b2, 300_000)

    def test_mark_with_reason_sets_reset_time(self):
        """mark_rate_limited_with_reason sets rate_limit_reset_times."""
        mark_rate_limited_with_reason(
            self.account, "gemini", "gemini-cli", None,
            RATE_LIMIT_REASON_SERVER_ERROR,
        )
        key = "gemini-cli"
        reset = self.account.rate_limit_reset_times.get(key)
        self.assertIsNotNone(reset)
        assert reset is not None
        now = time.time() * 1000
        self.assertGreaterEqual(reset, now)

    def test_default_not_cooling_down(self):
        """Fresh account with no cooldown → not cooling down."""
        self.assertIsNone(self.account.cooling_down_until)
        self.assertFalse(is_account_cooling_down(self.account))

    def test_future_cooling_down_until_returns_true(self):
        """Future cooling_down_until → is_account_cooling_down returns True."""
        self.account.cooling_down_until = time.time() * 1000 + 60_000
        self.assertTrue(is_account_cooling_down(self.account))

    def test_past_cooling_down_until_returns_false_and_clears(self):
        """Past cooling_down_until → returns False and clears fields."""
        self.account.cooling_down_until = time.time() * 1000 - 60_000
        self.account.cooldown_reason = "network-error"
        self.assertFalse(is_account_cooling_down(self.account))
        # Should be cleared
        self.assertIsNone(self.account.cooling_down_until)
        self.assertIsNone(self.account.cooldown_reason)

    def test_mark_with_reason_retry_after_override(self):
        """mark_rate_limited_with_reason with explicit retry_after_ms."""
        backoff = mark_rate_limited_with_reason(
            self.account, "gemini", "antigravity", None,
            RATE_LIMIT_REASON_UNKNOWN,
            retry_after_ms=5000,
        )
        self.assertEqual(backoff, 5000)

    def test_mark_with_reason_ttl_reset(self):
        """When last failure is beyond TTL, failures reset to 1."""
        self.account.consecutive_failures = 10
        # Set last failure time far in the past (beyond FAILURE_TTL_MS)
        self.account.last_failure_time = time.time() * 1000 - 7_200_000  # 2 hours ago
        mark_rate_limited_with_reason(
            self.account, "gemini", "antigravity", None,
            RATE_LIMIT_REASON_QUOTA_EXHAUSTED,
        )
        # Should have reset to 1 (not 11)
        self.assertEqual(self.account.consecutive_failures, 1)

    def test_mark_with_reason_ttl_not_reset_when_recent(self):
        """When last failure is within TTL, failures accumulate."""
        self.account.consecutive_failures = 3
        self.account.last_failure_time = time.time() * 1000 - 5_000  # 5 seconds ago
        mark_rate_limited_with_reason(
            self.account, "gemini", "antigravity", None,
            RATE_LIMIT_REASON_QUOTA_EXHAUSTED,
        )
        self.assertEqual(self.account.consecutive_failures, 4)


class TestRateLimitTracker(unittest.TestCase):
    def test_dedup_window_prunes_expired_keys(self):
        tracker = RateLimitTracker()

        with patch("antigravity_auth.accounts.ratelimit.now_ms", return_value=1_000):
            for index in range(5):
                self.assertFalse(tracker.is_duplicate(index, "gemini-antigravity"))
            self.assertEqual(len(tracker._dedup_window), 5)

        with patch("antigravity_auth.accounts.ratelimit.now_ms", return_value=7_001):
            self.assertFalse(tracker.is_duplicate(99, "gemini-antigravity"))

        self.assertEqual(tracker._dedup_window, {"99:gemini-antigravity": 7_001})

    def test_dedup_window_keeps_recent_key_as_duplicate(self):
        tracker = RateLimitTracker()

        with patch("antigravity_auth.accounts.ratelimit.now_ms", return_value=1_000):
            self.assertFalse(tracker.is_duplicate(0, "claude"))

        with patch("antigravity_auth.accounts.ratelimit.now_ms", return_value=5_999):
            self.assertTrue(tracker.is_duplicate(0, "claude"))
            self.assertEqual(tracker._dedup_window["0:claude"], 1_000)


if __name__ == "__main__":
    unittest.main()
