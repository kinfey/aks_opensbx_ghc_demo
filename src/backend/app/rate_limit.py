import asyncio
import time
from collections import defaultdict, deque


class SessionRateLimiter:
    def __init__(self, requests_per_minute: int) -> None:
        self._limit = requests_per_minute
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, session_id: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60
        async with self._lock:
            history = self._requests[session_id]
            while history and history[0] < cutoff:
                history.popleft()
            if len(history) >= self._limit:
                return False
            history.append(now)
            return True

    async def remove(self, session_id: str) -> None:
        async with self._lock:
            self._requests.pop(session_id, None)
