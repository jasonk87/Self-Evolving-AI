import asyncio
import time

class AsyncTokenBucket:
    """
    An asynchronous Token Bucket rate limiter.
    """
    def __init__(self, capacity: int, refill_rate: float):
        """
        :param capacity: Maximum number of tokens in the bucket.
        :param refill_rate: Number of tokens added per second.
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        """
        Acquire 'tokens' from the bucket, waiting if necessary.
        """
        while True:
            async with self._lock:
                self._refill()
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return
                # Calculate sleep time required to get enough tokens
                needed_tokens = tokens - self.tokens
                sleep_time = needed_tokens / self.refill_rate
            
            # Wait outside the lock so other tasks might acquire smaller amounts
            await asyncio.sleep(sleep_time)

    def _refill(self) -> None:
        """
        Refill the bucket based on elapsed time.
        """
        now = time.monotonic()
        elapsed = now - self.last_refill
        
        new_tokens = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + new_tokens)
        self.last_refill = now

# Global instance for LLM calls (e.g. 15 requests per minute -> 0.25 tokens/sec, capacity 5)
global_llm_rate_limiter = AsyncTokenBucket(capacity=10, refill_rate=1.0)
