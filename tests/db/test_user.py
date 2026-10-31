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


def test_get_user_by_telegram_id_returns_matching_user(loop, aengine):
    user = loop.run_until_complete(get_user(aengine, telegram_id=USER_B["telegram_id"]))

    assert user is not None
    assert user.id == USER_B["id"]


def test_get_user_by_user_id_returns_matching_user(loop, aengine):
    user = loop.run_until_complete(get_user(aengine, user_id=USER_A["id"]))

    assert user is not None
    assert user.id == USER_A["id"]


def test_get_user_requires_an_identifier(loop, aengine):
    with pytest.raises(ValueError):
        loop.run_until_complete(get_user(aengine))
