import asyncio
import json
from dataclasses import dataclass
from typing import overload, Any, Callable, Type
import functools
import niquests
import pydantic
import random

from polarsen.logs import logs

__all__ = (
    "parse_thinking",
    "parse_json_response",
    "JsonResponseError",
    "TooManyRequestsError",
    "QuotaExceededError",
    "retry_async",
)


def parse_thinking(resp: str, key: str = "think") -> tuple[str, str | None]:
    """
    Remove the thinking part from the response if it exists.
    Usually, the thinking part is between "<think></think>".
    """
    if f"<{key}>" in resp and f"</{key}>" in resp:
        thinking = resp.split(f"<{key}>")[1].split(f"</{key}>")[0].strip()
        resp = resp.split(f"</{key}>")[1]
    else:
        thinking = None
    return resp, thinking


@overload
def parse_json_response(
    resp: str, *, thinking_key: None = None, model: None = None
) -> tuple[dict | list[dict], str | None]: ...


@overload
def parse_json_response[T](
    resp: str, *, thinking_key: str | None = None, model: pydantic.TypeAdapter[T]
) -> tuple[T | list[T], str | None]: ...


class JsonResponseError(Exception):
    """
    Custom exception for JSON response parsing errors.
    """

    def __init__(
        self, message: str, resp: str | None = None, result: Any | None = None, thinking_text: str | None = None
    ):
        super().__init__(message)
        self.message = message
        self.resp = resp
        self.result = result
        self.thinking_text = thinking_text


def parse_json_response[T](
    resp: Any, *, thinking_key: str | None = None, model: pydantic.TypeAdapter[T] | None = None
) -> tuple[list[T] | T | dict | dict, str | None]:
    """
    Parse the JSON response from the AI model.
    """
    thinking_text: str | None = None

    if isinstance(resp, str):
        resp = resp.strip().lstrip("```json").rstrip("```")
    if thinking_key is not None:
        _thinking = False
        resp, thinking_text = parse_thinking(resp, key=thinking_key)
        if thinking_text:
            _thinking = True
    try:
        result = json.loads(resp)
    except json.JSONDecodeError:
        raise JsonResponseError("Failed to parse JSON response", resp=resp, thinking_text=thinking_text)

    if model is not None:
        try:
            result = (
                [model.validate_python(x) for x in result]
                if isinstance(result, list)
                else model.validate_python(result)
            )
        except pydantic.ValidationError:
            raise JsonResponseError(
                f"Failed to validate response with model {model}", result=result, thinking_text=thinking_text
            )

    return result, thinking_text


@dataclass
class TooManyRequestsError(Exception):
    """Retry delay in seconds."""

    retry_delay: int
    message: str | None = None
    response: niquests.Response | None = None

    def __post_init__(self):
        if not self.response:
            return

        logs.debug("Too many request headers", self.response.headers)
        try:
            logs.debug("Too many request JSON body", self.response.json())
        except Exception:
            logs.debug("Too many request text Body", self.response.headers)


@dataclass
class QuotaExceededError(Exception):
    """Raised when API quota is exceeded (not retryable)."""

    body: dict


def check_http_response(resp: niquests.Response) -> dict:
    try:
        resp.raise_for_status()
    except niquests.HTTPError as e:
        logs.error(resp.json())
        raise e
    return resp.json()


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
                    if isinstance(e, TooManyRequestsError):
                        actual_delay = e.retry_delay * (0.5 + random.random() if jitter else 1)
                        logs.debug(
                            f"Function {func.__name__} received TooManyRequestsError on attempt {attempt}/{max_attempts}. "
                            f"Retrying in {actual_delay:.2f} seconds..."
                        )
                        _backoff_factor = None
                    else:
                        actual_delay = current_delay * (0.5 + random.random() if jitter else 1)
                        logs.warning(
                            f"Function {func.__name__} failed on attempt {attempt}/{max_attempts}. "
                            f"Error: {type(e).__name__}: {e}. "
                            f"Retrying in {actual_delay:.2f} seconds..."
                        )
                        current_delay *= backoff_factor
                        _backoff_factor = backoff_factor

                    # Call retry callback if provided
                    if on_retry is not None:
                        on_retry(e, attempt, actual_delay)

                    await asyncio.sleep(actual_delay)
                    if _backoff_factor is not None:
                        current_delay *= _backoff_factor
                except Exception:
                    # Non-retryable exception - let caller handle logging
                    raise

            # This should never be reached, but just in case
            if last_exception and reraise_on_final_attempt:
                raise last_exception
            return None

        return wrapper

    return decorator
