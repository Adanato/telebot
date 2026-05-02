"""Tests for the async RateLimiter."""

from __future__ import annotations

import time
import unittest
from unittest.mock import AsyncMock, patch

from course_scout.infrastructure.rate_limiter import RateLimiter


class TestRateLimiter(unittest.IsolatedAsyncioTestCase):
    @patch("course_scout.infrastructure.rate_limiter.asyncio.sleep", new_callable=AsyncMock)
    async def test_rate_limiter_waits(self, mock_sleep):
        """If the previous call was very recent, acquire() awaits asyncio.sleep."""
        limiter = RateLimiter(rpm=60000)  # interval = 0.001s
        # Force a future last_request_time so wait_time is huge.
        limiter.last_request_time = time.time() + 10.0

        await limiter.acquire()

        self.assertTrue(mock_sleep.called)
        args, _ = mock_sleep.call_args
        self.assertGreater(args[0], 9.0)

    async def test_rpm_attribute(self):
        """RateLimiter exposes its configured rpm."""
        limiter = RateLimiter(rpm=42)
        self.assertEqual(limiter.rpm, 42)
