import pytest
from ..utils import clean_pg_fn


@pytest.fixture(scope="function", autouse=True)
def clean_pg(engine):
    clean_pg_fn(engine)
    yield
