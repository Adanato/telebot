import asyncio
import time


class RateLimiter:
    """Async-aware rate limiter for API calls.

    Enforces a maximum number of requests per minute (RPM) WITHOUT blocking the
    event loop. The previous implementation used `time.sleep` inside a threading
    lock, which froze all parallel tasks during throttle waits.
    """

    def __init__(self, rpm: int = 15):
        """Initialize with requests per minute limit."""
        self.rpm = rpm
        self.interval = 60.0 / rpm
        self.last_request_time = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Wait (asynchronously) if necessary to comply with the rate limit."""
        async with self._lock:
            current_time = time.time()
            elapsed = current_time - self.last_request_time

            if elapsed < self.interval:
                wait_time = self.interval - elapsed
                await asyncio.sleep(wait_time)

            self.last_request_time = time.time()
