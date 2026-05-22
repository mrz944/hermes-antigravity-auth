from __future__ import annotations

import unittest

from antigravity_auth.accounts.rotation import HealthScoreTracker


class TestHealthScoreTracker(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = HealthScoreTracker()

    def test_initial_score(self) -> None:
        self.assertEqual(self.tracker.get_score(0), 70)

    def test_success_increases(self) -> None:
        self.tracker.record_success(0)
        self.assertEqual(self.tracker.get_score(0), 71)

    def test_rate_limit_decreases(self) -> None:
        self.tracker.record_rate_limit(0)
        self.assertEqual(self.tracker.get_score(0), 60)

    def test_failure_decreases_more(self) -> None:
        self.tracker.record_failure(0)
        self.assertEqual(self.tracker.get_score(0), 50)

    def test_score_never_below_zero(self) -> None:
        for _ in range(10):
            self.tracker.record_failure(0)
        self.assertGreaterEqual(self.tracker.get_score(0), 0)

    def test_score_never_above_max(self) -> None:
        for _ in range(50):
            self.tracker.record_success(0)
        self.assertLessEqual(self.tracker.get_score(0), 100)

    def test_min_usable(self) -> None:
        self.assertTrue(self.tracker.is_usable(0))
        self.tracker.record_failure(0)
        self.tracker.record_failure(0)
        self.assertFalse(self.tracker.is_usable(0))

    def test_custom_config(self) -> None:
        tracker = HealthScoreTracker(config={"initial": 50, "min_usable": 40})
        self.assertEqual(tracker.get_score(0), 50)
        self.assertTrue(tracker.is_usable(0))
        tracker.record_failure(0)
        self.assertEqual(tracker.get_score(0), 30)
        self.assertFalse(tracker.is_usable(0))

    def test_independent_accounts(self) -> None:
        self.tracker.record_success(0)
        self.assertEqual(self.tracker.get_score(0), 71)
        self.assertEqual(self.tracker.get_score(1), 70)


if __name__ == "__main__":
    unittest.main()
