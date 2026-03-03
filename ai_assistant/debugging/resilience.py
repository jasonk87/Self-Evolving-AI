import asyncio
import functools
import logging
import random
import time
import traceback
import json
from typing import Optional, Dict, Any, Callable, TypeVar, Coroutine

# Configure basic logging if not already configured by the application's entry point
# This is a basic configuration; a real application might configure logging more centrally.
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')

logger = logging.getLogger(__name__)

# Type variables for generic decorators
T = TypeVar('T')
CallableT = TypeVar('CallableT', bound=Callable[..., Any])
R = TypeVar('R') # Return type for async coroutine

def retry_with_backoff(
    retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    jitter: bool = True
) -> Callable[[CallableT], CallableT]:
    """
    A decorator to retry a function with exponential backoff.

    Args:
        retries: Maximum number of retries.
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay in seconds.
        jitter: Whether to add random jitter to the delay.
    """
    def decorator(func: CallableT) -> CallableT:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any: # Using Any for return type with Coroutine
                last_exception: Optional[Exception] = None
                for attempt in range(retries + 1): # retries=3 means 1 initial call + 3 retries = 4 total attempts
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        last_exception = e
                        if attempt == retries:
                            logger.error(
                                f"Async function {func.__name__} failed after {attempt + 1} attempts. Re-raising last exception: {type(e).__name__}: {e}"
                            )
                            raise
                        
                        delay = min(max_delay, base_delay * (2 ** attempt))
                        if jitter:
                            delay += random.uniform(0, base_delay / 2.0) # Corrected to float division
                        
                        logger.warning(
                            f"Async function {func.__name__} failed (attempt {attempt + 1}/{retries + 1}). "
                            f"Retrying in {delay:.2f} seconds. Error: {type(e).__name__}: {e}"
                        )
                        await asyncio.sleep(delay)
                # This part should not be reached if logic is correct
                if last_exception: # Should have been re-raised
                    raise last_exception 
                return None # Should be unreachable
            return async_wrapper # type: ignore
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exception: Optional[Exception] = None
                for attempt in range(retries + 1): # retries=3 means 1 initial call + 3 retries = 4 total attempts
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        last_exception = e
                        if attempt == retries:
                            logger.error(
                                f"Sync function {func.__name__} failed after {attempt + 1} attempts. Re-raising last exception: {type(e).__name__}: {e}"
                            )
                            raise
                        
                        delay = min(max_delay, base_delay * (2 ** attempt))
                        if jitter:
                            delay += random.uniform(0, base_delay / 2.0) # Corrected to float division
                            
                        logger.warning(
                            f"Sync function {func.__name__} failed (attempt {attempt + 1}/{retries + 1}). "
                            f"Retrying in {delay:.2f} seconds. Error: {type(e).__name__}: {e}"
                        )
                        time.sleep(delay)
                # This part should not be reached if logic is correct
                if last_exception: # Should have been re-raised
                    raise last_exception
                return None # Should be unreachable
            return sync_wrapper # type: ignore
    return decorator # type: ignore

def log_critical_error(
    exception: Exception,
    message: str,
    context_info: Optional[Dict[str, Any]] = None
) -> None:
    """
    Logs a critical error with detailed information.

    Args:
        exception: The exception object.
        message: A custom message describing the error context.
        context_info: Optional dictionary containing contextual information.
    """
    tb_str = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
    
    log_message = f"CRITICAL ERROR: {message}\n"
    log_message += f"Exception Type: {type(exception).__name__}\n"
    log_message += f"Exception Message: {str(exception)}\n"
    
    if context_info:
        try:
            # Use default=str to handle common non-serializable types like datetime
            context_str = json.dumps(context_info, indent=2, default=str)
            log_message += f"Context Info:\n{context_str}\n"
        except TypeError as te: # Catch specific error if json.dumps fails with default=str
            logger.error(f"Could not serialize context_info to JSON for critical error logging. Error: {te}")
            log_message += f"Context Info (raw, serialization failed):\n{str(context_info)}\n"
            
    log_message += f"Traceback:\n{tb_str}"
    
    # Using CRITICAL level for this function as per its name
    logger.critical(log_message)

if __name__ == '__main__':
    # Example Usage of retry_with_backoff

    # --- Synchronous Example ---
    fail_sync_attempts_global = 0
    @retry_with_backoff(retries=3, base_delay=0.1, max_delay=0.5, jitter=True)
    def might_fail_sync(fail_times: int) -> str:
        global fail_sync_attempts_global
        if fail_sync_attempts_global < fail_times:
            fail_sync_attempts_global += 1
            logger.info(f"might_fail_sync: attempt count {fail_sync_attempts_global}, going to raise error")
            raise ValueError(f"Simulated sync failure on attempt count {fail_sync_attempts_global}")
        return f"Successfully executed might_fail_sync after {fail_sync_attempts_global} initial failures."

    # --- Asynchronous Example ---
    fail_async_attempts_global = 0
    @retry_with_backoff(retries=3, base_delay=0.1, max_delay=0.5, jitter=True)
    async def might_fail_async(fail_times: int) -> str:
        global fail_async_attempts_global
        if fail_async_attempts_global < fail_times:
            fail_async_attempts_global += 1
            logger.info(f"might_fail_async: attempt count {fail_async_attempts_global}, going to raise error")
            raise ValueError(f"Simulated async failure on attempt count {fail_async_attempts_global}")
        return f"Successfully executed might_fail_async after {fail_async_attempts_global} initial failures."

    async def main_examples():
        print("\n--- Testing synchronous retry ---")
        # Test 1: Succeeds on 3rd attempt (2 failures)
        global fail_sync_attempts_global
        fail_sync_attempts_global = 0
        try:
            print(f"Result: {might_fail_sync(2)}") # Should succeed after 2 failures
        except ValueError as e:
            print(f"Caught unexpected error: {e}")

        # Test 2: Fails after all retries (4 failures)
        fail_sync_attempts_global = 0
        try:
            print(might_fail_sync(4)) # Should fail after 3 retries (4 total attempts)
        except ValueError as e:
            print(f"Caught expected error after retries: {e}")

        print("\n--- Testing asynchronous retry ---")
        # Test 3: Async succeeds on 3rd attempt (2 failures)
        global fail_async_attempts_global
        fail_async_attempts_global = 0
        try:
            print(f"Result: {await might_fail_async(2)}")
        except ValueError as e:
            print(f"Caught unexpected error during async test: {e}")

        # Test 4: Async fails after all retries (4 failures)
        fail_async_attempts_global = 0
        try:
            print(await might_fail_async(4))
        except ValueError as e:
            print(f"Caught expected error after async retries: {e}")

        print("\n--- Testing log_critical_error ---")
        try:
            x = 1 / 0
        except ZeroDivisionError as e:
            log_critical_error(
                e,
                "A test critical division error occurred.",
                context_info={"user_id": "test_user", "action": "divide_by_zero_test", "data": {"numerator": 1, "denominator": 0}}
            )
        
        class NonSerializableForTest:
            def __repr__(self):
                return "<NonSerializableForTest object>"

        try:
            raise TypeError("This is a test type error with non-serializable context.")
        except TypeError as e:
            log_critical_error(
                e,
                "Testing non-serializable context in log_critical_error.",
                context_info={"object": NonSerializableForTest(), "status": "problematic_serialization"}
            )

    if __name__ == '__main__':
        asyncio.run(main_examples())
