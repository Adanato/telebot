import threading
import time


class RateLimiter:
    """Synchronous Rate Limiter for Gemini API calls.

    Enforces a maximum number of requests per minute (RPM).
    """

    def __init__(self, rpm: int = 15):
        """Initialize with requests per minute limit."""
        self.rpm = rpm
        self.interval = 60.0 / rpm
        self.last_request_time = 0.0
        self._lock = threading.Lock()

    def acquire(self):
        """Wait if necessary to comply with the rate limit."""
        with self._lock:
            current_time = time.time()
            elapsed = current_time - self.last_request_time

            if elapsed < self.interval:
                wait_time = self.interval - elapsed
                # print(f"DEBUG: Rate limit hit. Waiting {wait_time:.2f}s")
                time.sleep(wait_time)

            self.last_request_time = time.time()

    def __enter__(self):
        """Enter context manager and acquire lease."""
        self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager."""
        pass
