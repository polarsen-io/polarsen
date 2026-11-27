import pytest
from tracktolib.pg_sync import insert_many

from polarsen.api.utils import get_user
from tests.data.db import gen_user

USER_A = {**gen_user(telegram_id="userA"), "id": 1}
USER_B = {**gen_user(telegram_id="userB"), "id": 2}


@pytest.fixture(autouse=True)
def setup(engine):
    with engine.cursor() as cur:
        insert_many(cur, "general.users", [USER_A, USER_B])
    engine.commit()


@pytest.mark.parametrize(
    "telegram_id,expected_id",
    [
        pytest.param(USER_A["telegram_id"], USER_A["id"], id="first-user"),
        pytest.param(USER_B["telegram_id"], USER_B["id"], id="other-user"),
        pytest.param("unknown", None, id="unknown"),
    ],
)
def test_get_user(loop, aengine, telegram_id, expected_id):
    user = loop.run_until_complete(get_user(aengine, telegram_id=telegram_id))
    assert (user.id if user else None) == expected_id
