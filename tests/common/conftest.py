import pytest
from ..utils import clean_pg_fn
from unittest.mock import AsyncMock


@pytest.fixture(scope="function", autouse=True)
def clean_pg(engine):
    clean_pg_fn(engine)
    yield


@pytest.fixture
def mock_session():
    """Create a mock niquests AsyncSession."""
    return AsyncMock()
