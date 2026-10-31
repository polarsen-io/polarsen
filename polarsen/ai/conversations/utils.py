import asyncio
import functools
import random
from typing import Any, Callable, Type

from polarsen.logs import logs

__all__ = ("retry_async",)


def retry_async(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Type[Exception] | tuple[Type[Exception], ...] = (Exception,),
    on_retry: Callable[[Exception, int, float], None] | None = None,
    reraise_on_final_attempt: bool = True,
):
    """
    Async retry decorator with exponential backoff and jitter.

    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff_factor: Multiplier for delay after each failed attempt
        jitter: Add random jitter to delay to avoid thundering herd
        exceptions: Exception types to retry on (tuple or single exception)
        on_retry: Optional callback function called on each retry
        reraise_on_final_attempt: Whether to reraise the exception on final failure
    """
    if isinstance(exceptions, type):
        exceptions = (exceptions,)

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            current_delay = delay

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_attempts:
                        if reraise_on_final_attempt:
                            raise
                        return None

                    # Calculate delay with optional jitter
                    actual_delay = current_delay
                    if jitter:
                        actual_delay *= 0.5 + random.random()  # Random between 0.5x and 1.5x

                    logs.warning(
                        f"Function {func.__name__} failed on attempt {attempt}/{max_attempts}. "
                        f"Error: {type(e).__name__}: {e}. "
                        f"Retrying in {actual_delay:.2f} seconds..."
                    )

                    # Call retry callback if provided
                    if on_retry is not None:
                        on_retry(e, attempt, actual_delay)

                    await asyncio.sleep(actual_delay)
                    current_delay *= backoff_factor
                except Exception:
                    # Non-retryable exception - let caller handle logging
                    raise

            # This should never be reached, but just in case
            if last_exception and reraise_on_final_attempt:
                raise last_exception
            return None

        return wrapper

    return decorator
