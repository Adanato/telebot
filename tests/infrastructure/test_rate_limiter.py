import unittest
from unittest.mock import patch, MagicMock
import time

from telebot.infrastructure.rate_limiter import RateLimiter


class TestRateLimiter(unittest.TestCase):
    @patch("time.sleep")
    def test_rate_limiter_waits(self, mock_sleep):
        # We don't mock time.time here, instead we'll force a wait
        limiter = RateLimiter(rpm=60000)  # Very fast
        limiter.last_request_time = time.time() + 10.0 # Future
        
        limiter.acquire()
        
        # Interval is 60/60000 = 0.001
        # elapsed is ~ -10.0
        # wait_time is 0.001 - (-10.0) = 10.001
        self.assertTrue(mock_sleep.called)
        args, _ = mock_sleep.call_args
        self.assertGreater(args[0], 9.0)

    def test_context_manager(self):
        limiter = RateLimiter(rpm=60)
        with patch.object(limiter, "acquire") as mock_acquire:
            with limiter:
                pass
            mock_acquire.assert_called_once()
