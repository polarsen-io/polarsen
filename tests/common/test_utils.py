from unittest.mock import AsyncMock, patch

import pytest

from polarsen.common.models.utils import TooManyRequestsError, retry_async


class TestRetryAsync:
    def test_succeeds_on_first_attempt(self, loop):
        call_count = 0

        @retry_async(max_attempts=3, delay=0.01)
        async def success_fn():
            nonlocal call_count
            call_count += 1
            return "success"

        result = loop.run_until_complete(success_fn())

        assert result == "success"
        assert call_count == 1

    def test_retries_on_failure_then_succeeds(self, loop):
        call_count = 0

        @retry_async(max_attempts=3, delay=0.01)
        async def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("fail")
            return "success"

        result = loop.run_until_complete(fail_then_succeed())

        assert result == "success"
        assert call_count == 3

    def test_raises_after_max_attempts(self, loop):
        call_count = 0

        @retry_async(max_attempts=3, delay=0.01)
        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ValueError("always fails")

        with pytest.raises(ValueError, match="always fails"):
            loop.run_until_complete(always_fail())

        assert call_count == 3

    @pytest.mark.parametrize(
        "fail_until,expected_calls,should_succeed",
        [
            pytest.param(2, 2, True, id="succeeds_after_one_retry"),
            pytest.param(99, 3, False, id="raises_after_max_attempts"),
        ],
    )
    def test_too_many_requests_retry(self, loop, fail_until, expected_calls, should_succeed):
        call_count = 0

        @retry_async(max_attempts=3, delay=0.01, jitter=False)
        async def rate_limited():
            nonlocal call_count
            call_count += 1
            if call_count < fail_until:
                raise TooManyRequestsError(retry_delay=1)
            return "success"

        with patch("polarsen.common.models.utils.asyncio.sleep", new_callable=AsyncMock):
            if should_succeed:
                result = loop.run_until_complete(rate_limited())
                assert result == "success"
            else:
                with pytest.raises(TooManyRequestsError):
                    loop.run_until_complete(rate_limited())

        assert call_count == expected_calls

    def test_does_not_retry_non_matching_exceptions(self, loop):
        call_count = 0

        @retry_async(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        async def raise_type_error():
            nonlocal call_count
            call_count += 1
            raise TypeError("not retried")

        with pytest.raises(TypeError, match="not retried"):
            loop.run_until_complete(raise_type_error())

        assert call_count == 1  # No retry for TypeError
