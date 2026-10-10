import datetime as dt
import itertools
import json
import time
import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict, Unpack, cast

import asyncpg

if TYPE_CHECKING:
    from niquests import AsyncSession
    from openai.types import chat as openai_chat_types

import msgspec
from mistralai.client import models as mistral_models
from rich.progress import track

from polarsen.common.models import gemini, grok, mistral, openai
from polarsen.common.models.utils import parse_json_response
from polarsen.common.utils import AISource, setup_session_model
from polarsen.db import (
    BatchJob,
    GroupMethod,
    MessageGroup,
    MessageGroupChat,
    Requests,
    UsageToken,
    get_unique_identifier,
)
from polarsen.logs import logs

__all__ = (
    "SEGMENTATION_MODEL",
    "MessageLite",
    "ParamsV2",
    "apply_conversation_segmentation",
    "get_messages_by_dates",
    "process_gemini_batch_result",
)

GROUPING_V2 = GroupMethod(name="v2", internal_code="v2")

BASE_TEMP = 0

SEGMENTATION_MODEL = "gemini-2.5-flash-preview-09-2025"


class MessageLite(TypedDict):
    id: int
    u: int  # User id
    m: str  # Message content
    r_id: int | None  # Reply to message id
    s: str  # sent at UTC
    username: str  # Username


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
    return (msgspec.convert(dict(x), MessageLite) for x in _data), len(_data)


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
    from mistralai.client.models import (
        ChatCompletionRequestTypedDict,
        ResponseFormatTypedDict,
        SystemMessageTypedDict,
        UserMessageTypedDict,
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


def _prepare_gemini_contents(
    messages: list[MessageLite],
    users: dict,
):
    """
    Prepare Gemini content objects for segmentation.
    """
    from google.genai.types import Content, Part

    return [
        Content(
            role="user",
            parts=[
                Part.from_text(text="[MESSAGES]" + json.dumps(messages) + "[/MESSAGES]"),
                Part.from_text(text="[USERS]" + json.dumps(users) + "[/USERS]"),
            ],
        )
    ]


def _prepare_gemini_config(
    lang: str,
    temperature: float | None = None,
    seed: int | None = 2604,
    disable_thinking: bool = True,
):
    """
    Prepare Gemini config for segmentation.
    """
    from google.genai.types import Content, GenerateContentConfig, Part, ThinkingConfig

    return GenerateContentConfig(
        temperature=temperature,
        seed=seed,
        system_instruction=Content(
            parts=[Part.from_text(text=_PROMPT.format(lang=lang, nb_min_messages=NB_MIN_MESSAGES))],
        ),
        thinking_config=ThinkingConfig(thinking_budget=0) if disable_thinking else None,
    )


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
    Fetch conversation segmentation from the Gemini model.
    """
    config = _prepare_gemini_config(lang, temperature, seed, disable_thinking)
    contents = _prepare_gemini_contents(messages, users)

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
    source: Literal["openai", "grok"] = "openai",
):
    """
    Fetch conversation segmentation from the OpenAI model.
    """
    match source:
        case "openai":
            fn = openai.fetch_chat_completion
        case "grok":
            fn = grok.fetch_chat_completion
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
    _messages, _ids, _users = [], set(), {}
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
        case "openai" | "grok":
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
        result, thinking_text = parse_json_response(resp, model=ConversationSegResult)
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
    batch: NotRequired[bool]


class _DayData(TypedDict):
    """Data for a single day's batch processing."""

    day: dt.date
    messages: list[MessageLite]
    ids: set[int]
    users: dict
    nb_messages: int


def _serialize_days_data(days_data: list[_DayData]) -> list[dict]:
    """Serialize days_data for storage in BatchJob meta (sets -> lists)."""
    return [
        {
            "day": dd["day"].isoformat(),
            "messages": dd["messages"],
            "ids": list(dd["ids"]),
            "users": dd["users"],
            "nb_messages": dd["nb_messages"],
        }
        for dd in days_data
    ]


def _deserialize_days_data(data: list[dict]) -> list[_DayData]:
    """Deserialize days_data from BatchJob meta (lists -> sets)."""
    return [
        {
            "day": dt.date.fromisoformat(dd["day"]),
            "messages": dd["messages"],
            "ids": set(dd["ids"]),
            "users": dd["users"],
            "nb_messages": dd["nb_messages"],
        }
        for dd in data
    ]


async def _run_group_messages_batch_gemini(
    conn: asyncpg.Connection,
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    model_name: str,
    method_id: int,
    days_data: list[_DayData],
    lang: str,
    temperature: float,
    disable_thinking: bool,
    run_id: uuid.UUID,
    show_progress: bool = False,
) -> str | None:
    """
    Submit a Gemini batch job for processing multiple days.

    Returns the Gemini job name (e.g., "batches/xxx") if successful, None otherwise.
    The job will be processed asynchronously by the listener.
    """
    if not days_data:
        return None

    # Prepare batch requests
    batch_requests: list[gemini.BatchRequest] = []
    config = _prepare_gemini_config(lang, temperature, disable_thinking=disable_thinking)

    for day_data in days_data:
        contents = _prepare_gemini_contents(day_data["messages"], day_data["users"])
        batch_requests.append(
            gemini.BatchRequest(
                contents=contents,
                config=config,
                key=day_data["day"].isoformat(),
            )
        )

    logs.info(f"Submitting batch request with {len(batch_requests)} days to Gemini")

    # Submit batch request (returns LRO response, not results)
    batch_response = await gemini.fetch_completions_batch(
        session,
        model=model_name,
        requests=batch_requests,
        display_name=f"segmentation-{chat_id}-{run_id}",
    )

    gemini_job_name = batch_response["name"]
    logs.info(f"Batch job submitted: {gemini_job_name}")

    # Save batch job reference to database
    batch_job = BatchJob.for_gemini_batch(
        user_id=user_id,
        chat_id=chat_id,
        model_name=model_name,
        gemini_job_name=gemini_job_name,
        days_data=_serialize_days_data(days_data),
        run_id=run_id,
        method_id=method_id,
        lang=lang,
        temperature=temperature,
        disable_thinking=disable_thinking,
    )
    await batch_job.save(conn)
    logs.info(f"Batch job saved to database with id={batch_job._id}")

    return gemini_job_name


async def process_gemini_batch_result(
    conn: asyncpg.Connection,
    batch_job: BatchJob,
    inlined_responses: list[dict],
) -> tuple[int, int, int, int]:
    """
    Process the results of a completed Gemini batch job.

    Args:
        conn: Database connection
        batch_job: The BatchJob record from our database
        inlined_responses: The inlined responses from the completed Gemini job

    Returns:
        Tuple of (nb_messages, nb_discussions, nb_input_tokens, nb_output_tokens)
    """
    meta = batch_job.meta
    chat_id = meta["chat_id"]
    method_id = meta["method_id"]
    run_id = batch_job.run_id
    user_id = batch_job.user_id
    disable_thinking = meta["disable_thinking"]

    # Deserialize days_data
    days_data = _deserialize_days_data(meta["days_data"])

    # Create mapping from day key to day data
    days_by_key: dict[str, _DayData] = {dd["day"].isoformat(): dd for dd in days_data}

    # Create mapping from response key to response
    # Each inlined response has a "metadata" with "key" and "response" with the actual response
    responses_by_key: dict[str, dict] = {}
    for resp in inlined_responses:
        key = resp.get("metadata", {}).get("key")
        if key:
            responses_by_key[key] = resp

    nb_messages, nb_discussions, nb_input_tokens, nb_output_tokens = 0, 0, 0, 0

    for day_key, day_data in days_by_key.items():
        _day = day_data["day"]
        _ids = day_data["ids"]
        _nb_messages = day_data["nb_messages"]
        nb_messages += _nb_messages

        if day_key not in responses_by_key:
            logs.error(f"Missing result for day {day_key} in batch response")
            continue

        resp_data = responses_by_key[day_key]

        # Extract response content and usage
        response = resp_data.get("response", {})
        candidates = response.get("candidates", [])
        usage_metadata = response.get("usageMetadata", {})

        if not candidates:
            logs.error(f"No candidates in response for day {day_key}")
            continue

        # Extract text content
        content_parts = candidates[0].get("content", {}).get("parts", [])
        content_text = ""
        for part in content_parts:
            if "text" in part:
                content_text += part["text"]

        # Extract token usage
        token: UsageToken = {
            "total": usage_metadata.get("totalTokenCount", 0),
            "input": usage_metadata.get("promptTokenCount", 0),
            "output": usage_metadata.get("candidatesTokenCount", 0),
            "cached": usage_metadata.get("cachedContentTokenCount", 0),
        }

        # Parse the response
        try:
            result, thinking_text = parse_json_response(content_text, model=ConversationSegResult)
        except Exception as e:
            logs.error(f"Failed to parse response for day {day_key}: {e}")
            continue

        discussions = result if isinstance(result, list) else [result]

        # Validate IDs
        res_ids = {_id for res in discussions for _id in res["ids"]}
        invalid_ids = res_ids - _ids
        missing_ids = _ids - res_ids

        if invalid_ids:
            logs.warning(f"Invalid ids found for day {day_key}: {invalid_ids}")

        # Save request metadata
        _meta: dict = {"thinking": not disable_thinking, "batch": True, "gemini_batch": True}
        if thinking_text is not None:
            _meta["thinking_text"] = thinking_text
        await Requests.load(
            "completion",
            user_id=user_id,
            token=token,
            payload={"batch": True, "day": day_key},
            meta=_meta,
            run_id=run_id,
        ).save(conn)

        # Save discussions
        group_meta = {"day": day_key}
        for discussion in discussions:
            _group = MessageGroup(
                chat_id=chat_id,
                group_method_id=method_id,
                summary=discussion["summary"],
                title=discussion["title"],
                internal_code=get_unique_identifier(discussion["ids"], day_key + str(run_id)),
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
            f"missing ids: {len(missing_ids)}"
        )

    # Update batch job meta with results
    batch_job.meta["processed_messages"] = nb_messages
    batch_job.meta["total_discussions"] = nb_discussions

    return nb_messages, nb_discussions, nb_input_tokens, nb_output_tokens


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
    return {x["day"] for x in rows}


async def _get_available_days(
    conn: asyncpg.Connection, chat_id: int, from_date: dt.date | None = None
) -> list[dt.date]:
    """
    Returns the list of distinct days that have messages in the chat.
    Optionally filters to days >= from_date.
    """
    rows = await conn.fetch(
        """
        SELECT distinct cm.sent_at::date as d
        from general.chat_messages cm
        where cm.chat_id = $1
          and ($2::date is null or cm.sent_at::date >= $2)
        order by d
        """,
        chat_id,
        from_date,
    )
    return [x["d"] for x in rows]


async def _run_group_messages(
    conn: asyncpg.Connection,
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    source: AISource,
    model_name: str | None,
    agent_name: str | None,
    method_id: int,
    days: list[dt.date],
    lang: str,
    temperature: float,
    disable_thinking: bool,
    run_id: uuid.UUID,
    show_progress: bool = False,
    force: bool = False,
) -> tuple[int, int, int, int]:
    """
    Process days sequentially (one request per day).
    Returns (nb_messages, nb_discussions, nb_input_tokens, nb_output_tokens).
    """
    nb_messages, nb_discussions, nb_input_tokens, nb_output_tokens = 0, 0, 0, 0

    for day in track(days, disable=not show_progress, show_speed=True, description="Days..."):
        _day: dt.date = day
        messages, _nb_messages = await get_messages_by_dates(conn, dates=[_day], chat_id=chat_id, force=force)
        nb_messages += _nb_messages
        start = time.time()

        discussions, missing_ids, token, invalid_ids = await apply_conversation_segmentation(
            session,
            messages,
            conn=conn,
            lang=lang,
            temperature=temperature,
            source=source,
            agent_name=agent_name,
            model_name=model_name,
            raise_invalid_ids=False,
            run_id=run_id,
            disable_thinking=disable_thinking,
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
                group_method_id=method_id,
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

    return nb_messages, nb_discussions, nb_input_tokens, nb_output_tokens


async def _run_group_messages_batch(
    conn: asyncpg.Connection,
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    model_name: str,
    method_id: int,
    days: list[dt.date],
    lang: str,
    temperature: float,
    disable_thinking: bool,
    run_id: uuid.UUID,
    show_progress: bool = False,
    force: bool = False,
) -> str | None:
    """
    Submit days for Gemini batch API processing.

    Returns the Gemini job name if successful, None otherwise.
    Results will be processed asynchronously by the listener.
    """
    # Collect all days' data for batch processing
    logs.info(f"Batch mode enabled, collecting data for {len(days)} days")
    days_data: list[_DayData] = []

    # Fetch all messages in one query
    all_messages, _ = await get_messages_by_dates(conn, dates=days, chat_id=chat_id, force=force)
    all_messages_list = list(all_messages)

    # Group by date (messages are already sorted by sent_at)
    def get_date(msg: MessageLite) -> dt.date:
        return dt.datetime.fromisoformat(msg["s"]).date()

    for day, group in itertools.groupby(all_messages_list, key=get_date):
        _messages = list(group)
        _ids = {x["id"] for x in _messages}
        _users = {x["u"]: x["username"] for x in _messages}

        days_data.append(
            {
                "day": day,
                "messages": _messages,
                "ids": _ids,
                "users": _users,
                "nb_messages": len(_messages),
            }
        )

    # Submit batch job (returns job name, results processed asynchronously)
    return await _run_group_messages_batch_gemini(
        conn=conn,
        session=session,
        chat_id=chat_id,
        user_id=user_id,
        model_name=model_name,
        method_id=method_id,
        days_data=days_data,
        lang=lang,
        temperature=temperature,
        disable_thinking=disable_thinking,
        run_id=run_id,
        show_progress=show_progress,
    )


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
    _batch = params.get("batch", False)

    _method_id = await GROUPING_V2.upsert(conn)

    if not _days:
        from_date = params.get("from_date")
        processed_days = await _get_processed_days(conn, chat_id) if not force else set()
        all_days = await _get_available_days(conn, chat_id, from_date)
        nb_days = len(all_days)
        _days = [d for d in all_days if d not in processed_days]
    else:
        nb_days = len(_days)
        _days = list(_days)
    logs.info(f"Found {nb_days} days to process")

    run_id = uuid.uuid4()

    # Choose processing mode
    if _batch and source == "gemini":
        if model_name is None:
            raise ValueError("Model name must be provided for batch mode with Gemini")
        gemini_job_name = await _run_group_messages_batch(
            conn=conn,
            session=session,
            chat_id=chat_id,
            user_id=user_id,
            model_name=model_name,
            method_id=_method_id,
            days=_days,
            lang=lang,
            temperature=_temperature,
            disable_thinking=_disable_thinking,
            run_id=run_id,
            show_progress=show_progress,
            force=force,
        )
        logs.info(f"Batch job submitted: {gemini_job_name}. Results will be processed by listener.")
        return gemini_job_name

    nb_messages, nb_discussions, nb_input_tokens, nb_output_tokens = await _run_group_messages(
        conn=conn,
        session=session,
        chat_id=chat_id,
        user_id=user_id,
        source=source,
        model_name=model_name,
        agent_name=agent_name,
        method_id=_method_id,
        days=_days,
        lang=lang,
        temperature=_temperature,
        disable_thinking=_disable_thinking,
        run_id=run_id,
        show_progress=show_progress,
        force=force,
    )

    logs.info(
        f"Finished grouping {nb_messages} messages into {nb_discussions} discussions "
        f"({nb_input_tokens} input token, {nb_output_tokens} output token, run_id: {run_id})"
    )
