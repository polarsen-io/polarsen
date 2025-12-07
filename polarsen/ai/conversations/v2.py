from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from typing import TypedDict, Iterable, Unpack, NotRequired, Literal, cast, TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from mistralai import models as mistral_models
    from openai.types import chat as openai_chat_types
    from niquests import AsyncSession

from pydantic.type_adapter import TypeAdapter
from rich.progress import track

from polarsen.common.utils import setup_session_model, AISource
from polarsen.common.models import mistral, grok, gemini, self_hosted, openai
from polarsen.common.models.utils import parse_json_response
from polarsen.db import GroupMethod, Requests, MessageGroupChat, MessageGroup, get_unique_identifier, UsageToken
from polarsen.logs import logs

__all__ = ("get_messages_by_dates", "apply_conversation_segmentation", "MessageLite", "ParamsV2", "SEGMENTATION_MODEL")

GROUPING_V2 = GroupMethod(name="v2", internal_code="v2")

BASE_TEMP = 0

SEGMENTATION_MODEL = "gemini-2.5-flash-preview-05-20"


class MessageLite(TypedDict):
    id: int
    u: int  # User id
    m: str  # Message content
    r_id: int | None  # Reply to message id
    s: str  # sent at UTC
    username: str  # Username


_MessageLiteType = TypeAdapter(MessageLite)

_MESSAGES_BY_DATE = """
                    select cm.id,
                           cm.chat_user_id as u,
                           cu.username     as username,
                           cm.message      as m,
                           cm.reply_to_id  as r_id,
                           (sent_at at time zone 'UTC')::text as s
                    from general.chat_messages cm
                             left join general.chat_users cu on cu.id = cm.chat_user_id
                    where cm.message <> ''
                      and cm.sent_at::date = any ($1)
                      and cm.id in (select id from general.message_replies_view)
                      and cm.chat_id = $2
                    order by cm.sent_at asc
                    """


async def get_messages_by_dates(
    conn: asyncpg.Connection, chat_id: int, dates: list[dt.date], force: bool = False
) -> tuple[Iterable[MessageLite], int]:
    """
    Retrieve messages from the database for the given dates.
    Replies to messages of the filtered dates are also included.
    For instance, if the date is 2023-10-01, messages sent
    on 2023-10-01 and replies sent later to those messages are returned.
    """
    _data = await conn.fetch(_MESSAGES_BY_DATE, dates, chat_id)
    return (_MessageLiteType.validate_python(dict(x)) for x in _data), len(_data)


NB_MIN_MESSAGES = 3

_PROMPT = """
Group the messages inside [MESSAGES][/MESSAGES] by same topic conversation.
Here is how the data is structured:
- id is the message id
- u is the user id
- m is the message
- s when the message was sent 
- r_id is the reply to message id.
The messages are in chronological order.
Users are inside the [USER][/USER] tags.
Here is how the data is structured:
- id is the message id
- u is the user id
- username is the username
Output the result in JSON format with the following keys:
- title: the title of the conversation in {lang}.
- summary: a succinct summary of the conversation in {lang} (max 500 chars).
- ids: a list of message ids (minimum {nb_min_messages} messages) that belong to the conversation.
Guidelines:
- Classify ALL the messages inside [MESSAGES][/MESSAGES] tags.
- Do not invent ids that are not in the messages.
- Do not overlap the topics.
- ids MUST NOT have less than {nb_min_messages} integers.
- Make sure conversation has a unique title and summary.
- Keep the number of topics to a minimum.
- Do not start the title with "discussion" or "conversation".
- If a message can belong to multiple topics, assign it to the most relevant one based on the context.
Expected output:
"[{{"title": "Conversation title", "summary": "Conversation summary", "ids": [1,2,3]}}]"
"""


async def _fetch_mistral_segmentation(
    session: AsyncSession,
    messages: list[MessageLite],
    users: dict,
    lang: str,
    model_name: str | None = None,
    agent_id: str | None = None,
    response_format: mistral_models.ResponseFormats = "text",
    temperature: float = BASE_TEMP,
    random_seed: int | None = 2604,
):
    """
    Fetch conversation segmentation from the Mistral model.
    """
    from mistralai import (
        ResponseFormatTypedDict,
        SystemMessageTypedDict,
        UserMessageTypedDict,
        ChatCompletionRequestTypedDict,
    )

    _messages = "[MESSAGES]" + json.dumps(messages) + "[/MESSAGES]"
    _users = "[USERS]" + json.dumps(users) + "[/USERS]"

    _response_format = ResponseFormatTypedDict(type=response_format)
    if model_name is not None:
        req_messages = [
            SystemMessageTypedDict(content=_PROMPT.format(lang=lang, nb_min_messages=NB_MIN_MESSAGES), role="system"),
            UserMessageTypedDict(content=_messages, role="user"),
            UserMessageTypedDict(content=_users, role="user"),
        ]
        request = ChatCompletionRequestTypedDict(
            messages=req_messages,
            model=model_name,
            response_format=_response_format,
            random_seed=random_seed,
            # ResponseFormat(type=response_format)
            temperature=temperature,
        )
        resp, token, payload = await mistral.fetch_completion(session, request)
    elif agent_id is not None:
        req_messages = [mistral_models.UserMessage(content=_messages), mistral_models.UserMessage(content=_users)]
        request = mistral_models.AgentsCompletionRequestTypedDict(
            messages=req_messages,  # type: ignore
            agent_id=agent_id,
            response_format=_response_format,
            random_seed=random_seed,
        )
        resp, token, payload = await mistral.fetch_agent_completion(session, request)
    else:
        raise ValueError("Either model_name or agent_id must be provided")
    return resp, token, cast(dict, payload)


async def _fetch_gemini_segmentation(
    session: AsyncSession,
    messages: list[MessageLite],
    users: dict,
    lang: str,
    model_name: str,
    temperature: float | None = None,
    seed: int | None = 2604,
    disable_thinking: bool = True,
):
    """
    Fetch conversation segmentation from the Mistral model.
    """
    from google.genai.types import GenerateContentConfig, Content, Part, ThinkingConfig

    config = GenerateContentConfig(
        temperature=temperature,
        seed=seed,
        system_instruction=Content(
            parts=[Part.from_text(text=_PROMPT.format(lang=lang, nb_min_messages=NB_MIN_MESSAGES))],
        ),
        thinking_config=ThinkingConfig(thinking_budget=0) if disable_thinking else None,
    )
    contents = [
        Content(
            role="user",
            parts=[
                Part.from_text(text="[MESSAGES]" + json.dumps(messages) + "[/MESSAGES]"),
                Part.from_text(text="[USERS]" + json.dumps(users) + "[/USERS]"),
            ],
        )
    ]

    resp, token, payload = await gemini.fetch_completion(session, model=model_name, contents=contents, config=config)
    return resp, token, payload


async def _fetch_openai_segmentation(
    session: AsyncSession,
    messages: list[MessageLite],
    users: dict,
    lang: str,
    model_name: str,
    temperature: float = BASE_TEMP,
    seed: int | None = 2604,
    source: Literal["openai", "grok", "self_hosted"] = "openai",
):
    """
    Fetch conversation segmentation from the OpenAI model.
    """
    match source:
        case "openai":
            fn = openai.fetch_chat_completion
        case "grok":
            fn = grok.fetch_chat_completion
        case "self_hosted":
            fn = self_hosted.fetch_chat_completion
        case _:
            raise ValueError(f"Unknown source {source!r} for OpenAI segmentation")
    _messages = "[MESSAGES]" + json.dumps(messages) + "[/MESSAGES]"
    _users = "[USERS]" + json.dumps(users) + "[/USERS]"

    chat_messages: list[openai_chat_types.ChatCompletionMessageParam] = [
        {"role": "system", "content": _PROMPT.format(lang=lang, nb_min_messages=NB_MIN_MESSAGES)},
        {"role": "user", "content": _messages},
        {"role": "user", "content": _users},
    ]
    resp, token, payload = await fn(
        session,
        model=model_name,  # type: ignore
        messages=chat_messages,
        temperature=temperature,
        seed=seed,
    )
    return resp, token, payload


class ConversationSegResult(TypedDict):
    title: str
    summary: str
    ids: list[int]


_ConversationSegResultType = TypeAdapter(ConversationSegResult)


async def apply_conversation_segmentation(
    session: AsyncSession,
    messages: Iterable[MessageLite],
    source: AISource,
    lang: str,
    user_id: int,
    model_name: str | None = None,
    agent_name: str | None = None,
    conn: asyncpg.Connection | None = None,
    temperature: float = BASE_TEMP,
    seed: int | None = 2604,
    run_id: uuid.UUID | None = None,
    meta: dict | None = None,
    raise_invalid_ids: bool = True,
    disable_thinking: bool = True,
) -> tuple[list[ConversationSegResult], set[int], UsageToken, set[int]]:
    """
    Apply conversation segmentation to the messages.
    Returns a list of conversations, a set of message ids that were not classified,
    and the number of tokens used.
    """
    _messages, _ids, _users = [], set(), dict()
    for x in messages:
        _msg = {k: v for k, v in x.items() if v is not None}
        _messages.append(_msg)
        _ids.add(_msg["id"])
        _users[x["u"]] = _msg.pop("username")

    # Whether the model is thinking
    _thinking: bool | None = None
    start = time.time()
    match source:
        case "mistral":
            resp, token, payload = await _fetch_mistral_segmentation(
                session=session,
                messages=_messages,
                users=_users,
                model_name=model_name,
                agent_id=mistral.MISTRAL_AGENTS[agent_name] if agent_name else None,
                lang=lang,
                response_format="text",
                temperature=temperature,
                random_seed=seed,
            )
        case "gemini":
            if model_name is None:
                raise ValueError("Model name must be provided for gemini")
            resp, token, payload = await _fetch_gemini_segmentation(
                session=session,
                messages=_messages,
                users=_users,
                model_name=model_name,
                lang=lang,
                temperature=temperature,
                seed=seed,
                disable_thinking=disable_thinking,
            )
            _thinking = not disable_thinking
        case "openai" | "grok" | "self_hosted":
            if model_name is None:
                raise ValueError("Model name must be provided for openai")
            resp, token, payload = await _fetch_openai_segmentation(
                session=session,
                messages=_messages,
                users=_users,
                model_name=model_name,
                lang=lang,
                temperature=temperature,
                seed=seed,
                source=source,
            )
        case _:
            raise ValueError(f"Unknown model name {model_name!r}")

    thinking_text: str | None = None
    try:
        result, thinking_text = parse_json_response(resp, model=_ConversationSegResultType)
    finally:
        if conn:
            _meta: dict = {**(meta or {}), "elapsed": time.time() - start, "thinking": _thinking}
            if thinking_text is not None:
                _meta["thinking_text"] = thinking_text
            await Requests.load(
                "completion", user_id=user_id, token=token, payload=payload, meta=_meta, run_id=run_id
            ).save(conn)

    results = result if isinstance(result, list) else [result]

    res_ids = {_id for res in results for _id in res["ids"]}
    # Check that ids exists from the original messages
    _invalid_ids = set()
    for _id in res_ids:
        if _id not in _ids:
            if not raise_invalid_ids:
                _invalid_ids.add(_id)
            else:
                raise ValueError(f"ID {_id} not found in original messages")
    not_classified_ids = _ids - res_ids
    return results, not_classified_ids, token, _invalid_ids


class ParamsV2(TypedDict):
    days: NotRequired[list[dt.date]]
    from_date: NotRequired[dt.date]
    temperature: NotRequired[float]
    model_name: NotRequired[str]
    agent_name: NotRequired[str]
    disable_thinking: NotRequired[bool]


async def _get_processed_days(conn: asyncpg.Connection, chat_id: int) -> set[dt.date]:
    """
    Returns the set of days that have messages have already been processed.
    Not all messages of the day needs to be processed, just one message.
    """
    query = """
            select distinct (mg.meta ->> 'day') ::date as day
            from ai.message_group_chats mgc
                inner join general.chat_messages cm
            on mgc.msg_id = cm.id
                left join ai.message_groups mg on mg.id = mgc.group_id
            where cm.chat_id = $1
            order by day
            """
    rows = await conn.fetch(query, chat_id)
    return set(x["day"] for x in rows)


async def run_group_messages(
    conn: asyncpg.Connection,
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    show_progress: bool = False,
    force: bool = False,
    lang: str = "french",
    api_key: str | None = None,
    **params: Unpack[ParamsV2],
):
    """
    Group messages into discussions.
    Also compute the summary for each discussion in the specified `lang` language.
    """
    source, model_name, agent_name = setup_session_model(
        session,
        model_name=params.get("model_name"),
        agent_name=params.get("agent_name"),
        api_key=api_key,
    )

    _days = params.get("days")
    _temperature = params.get("temperature", BASE_TEMP)
    _disable_thinking = params.get("disable_thinking", True)

    _method_id = await GROUPING_V2.upsert(conn)

    if not _days:
        from_date = params.get("from_date")
        if not force:
            processed_days = await _get_processed_days(conn, chat_id)
        else:
            processed_days = set()
        days = await conn.fetch(
            """
            SELECT distinct cm.sent_at::date as d
            from general.chat_messages cm
            where cm.chat_id = $1
              and $2::date is null
               or cm.sent_at::date >= $2
            order by d
            """,
            chat_id,
            from_date,
        )
        nb_days = len(days)
        _days = (x["d"] for x in days if x["d"] not in processed_days)
    else:
        nb_days = len(_days)
    logs.info(f"Found {nb_days} days to process")

    nb_messages, nb_discussions, nb_input_tokens, nb_output_tokens = 0, 0, 0, 0
    run_id = uuid.uuid4()
    for day in track(_days, disable=not show_progress, show_speed=True, description="Days..."):
        _day: dt.date = day
        messages, _nb_messages = await get_messages_by_dates(conn, dates=[_day], chat_id=chat_id, force=force)
        nb_messages += _nb_messages
        start = time.time()

        discussions, missing_ids, token, invalid_ids = await apply_conversation_segmentation(
            session,
            messages,
            conn=conn,
            lang=lang,
            temperature=_temperature,
            source=source,
            agent_name=agent_name,
            model_name=model_name,
            raise_invalid_ids=False,
            run_id=run_id,
            disable_thinking=_disable_thinking,
            user_id=user_id,
        )
        if invalid_ids:
            logs.warning(f"Invalid ids found: {invalid_ids}")

        request_meta = {
            "run_id": run_id,
            "meta": {
                "day": _day.isoformat(),
                "chat_id": chat_id,
                "model_name": model_name,
                "invalid_ids": list(invalid_ids) if invalid_ids else None,
                "missing_ids": list(missing_ids) if missing_ids else None,
            },
        }
        await Requests.update(conn, request_meta)

        group_meta = {"day": _day.isoformat()}
        for discussion in track(discussions, disable=not show_progress, show_speed=True, description="Discussions..."):
            _group = MessageGroup(
                chat_id=chat_id,
                group_method_id=_method_id,
                summary=discussion["summary"],
                title=discussion["title"],
                internal_code=get_unique_identifier(discussion["ids"], _day.isoformat() + str(run_id)),
                meta=group_meta,
                run_id=run_id,
            )
            _group_id = await _group.upsert(conn)
            _group_messages = (
                MessageGroupChat(chat_id=chat_id, group_id=_group_id, msg_id=msg_id) for msg_id in discussion["ids"]
            )
            await MessageGroupChat.bulk_save(conn, _group_messages)
            if len(discussion["ids"]) < NB_MIN_MESSAGES:
                logs.warning(f"Discussion {discussion['title']} has less than {NB_MIN_MESSAGES} messages")

        nb_discussions += len(discussions)
        nb_input_tokens += token["input"]
        nb_output_tokens += token["output"]
        logs.debug(
            f"Day {_day} | messages: {_nb_messages} | "
            f"discussions: {len(discussions)} | "
            f"input token: {token['input']} | "
            f"output token: {token['output']} | "
            f"missing ids: {len(missing_ids)} "
            f"(run_id: {run_id}, "
            f"took {time.time() - start:.2f}s, "
            f"total input tokens: {nb_input_tokens}, "
            f"total output tokens: {nb_output_tokens})"
        )

    logs.info(
        f"Finished grouping {nb_messages} messages into {nb_discussions} discussions "
        f"({nb_input_tokens} input token, {nb_output_tokens} output token, run_id: {run_id})"
    )
