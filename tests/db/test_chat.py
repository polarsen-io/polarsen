import pytest
from tracktolib.pg_sync import fetch_all, insert_one

from tests.data import gen_telegram_group, gen_user

USER = gen_user()
SIMPLE_TELEGRAM_GROUP = gen_telegram_group()

EXPECTED_SIMPLE_TELEGRAM_GROUP = {
    "chat_users": 2,
    "chats": 1,
    "messages": 3,
}


@pytest.fixture(autouse=True)
def setup(engine):
    insert_one(engine, "general.users", USER)


@pytest.mark.parametrize(
    "data,expected", [pytest.param(SIMPLE_TELEGRAM_GROUP, EXPECTED_SIMPLE_TELEGRAM_GROUP, id="simple")]
)
def test_load_save_telegram_group(data, expected, loop, aengine, engine):
    from polarsen.db.chat import TelegramGroup

    group = TelegramGroup.load(data)

    loop.run_until_complete(group.save(aengine, created_by=USER["id"]))
    chat_users_db = fetch_all(engine, "SELECT * FROM general.chat_users")
    assert len(chat_users_db) == expected["chat_users"]
    chats_db = fetch_all(engine, "SELECT * FROM general.chats")
    assert len(chats_db) == expected["chats"]
    messages_db = fetch_all(engine, "SELECT * FROM general.chat_messages")
    assert len(messages_db) == expected["messages"]
