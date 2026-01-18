import time
from unittest.mock import patch, MagicMock
from telebot.infrastructure.rate_limiter import RateLimiter

def test_rate_limiter_init():
    limiter = RateLimiter(rpm=60)
    assert limiter.interval == 1.0

@patch('time.sleep')
@patch('time.time')
def test_rate_limiter_acquire_waits(mock_time, mock_sleep):
    limiter = RateLimiter(rpm=60) # 1 request per second
    
    # Init time far in future to avoid initial check issue if last_request_time is 0
    start_time = 1000.0
    
    # First call: time is 1000. Should set last_request_time=1000
    mock_time.return_value = start_time
    limiter.acquire()
    mock_sleep.assert_not_called()
    
    # Second call: time is 1000.5 (should wait 0.5s)
    mock_time.return_value = start_time + 0.5
    limiter.acquire()
    
    # Should calculate wait_time = 1.0 - 0.5 = 0.5
    mock_sleep.assert_called_with(0.5)

@patch('time.sleep')
@patch('time.time')
def test_rate_limiter_acquire_no_wait(mock_time, mock_sleep):
    limiter = RateLimiter(rpm=60)
    
    start_time = 2000.0
    
    # First call
    mock_time.return_value = start_time
    limiter.acquire()
    
    # Second call: time is 2001.5 (should not wait)
    mock_time.return_value = start_time + 1.5
    limiter.acquire()
    
    mock_sleep.assert_not_called()
