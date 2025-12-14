import os
from contextlib import asynccontextmanager
from typing import Any
import asyncpg
from collections.abc import Mapping

from fastapi import FastAPI, Depends, Query, Request
from fastapi.responses import JSONResponse
from starlette import status

import polarsen
from polarsen.common.chat import ChatSession
from polarsen.logs import logs
from polarsen.s3_utils import s3_file_upload, s3_delete_object
from polarsen.utils import compute_md5
from polarsen import env
from .data import User as UserDB, Question as QuestionDB
from polarsen.db import ChatUpload as ChatUploadDB
from .dependencies import connect_pg, get_conn, get_client, get_s3_client
from .models import NewUser, User, AIModel, AskQuestion, EmbeddingResult, Status, ChatUpload, ChatType
from .utils import APIException, get_user, ErrorCode

VERSION = polarsen.__version__


# See: https://github.com/pydantic/pydantic/issues/9406#issuecomment-2104224328
Mapping.register(asyncpg.Record)  # type: ignore[method-assign]


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with connect_pg(os.environ["_PG_URL"]):
        yield


app = FastAPI(
    version=VERSION,
    lifespan=lifespan,
)


@app.get("/version")
def read_root():
    """Return the current API version."""
    return {"version": VERSION}


@app.get("/health")
def health_check():
    """Health check endpoint for monitoring and load balancers."""
    return {"status": "ok"}


USER_TAG = "user"


@app.post("/users", tags=[USER_TAG])
async def _create_user(user: NewUser, conn=Depends(get_conn)) -> User:
    """
    Create or update a user.

    If a user with the same telegram_id already exists, their information will be updated.
    The user is automatically linked to any existing Telegram chats they belong to.
    """
    async with conn.transaction():
        user_id = await UserDB.upsert(
            conn,
            telegram_id=user.telegram_id,
            first_name=user.first_name,
            last_name=user.last_name,
            meta=user.meta,
            api_keys=user.api_keys,
        )
        data = await get_user(conn, user_id=user_id)
        if data is None:
            raise ValueError(f"Failed to create user with telegram_id {user.telegram_id}")
    return data


@app.post("/users/bulk", tags=[USER_TAG])
async def _create_users(users: list[NewUser], conn=Depends(get_conn)) -> Status:
    """
    Create or update multiple users in a single transaction.

    Each user is upserted individually. If a user already exists, their information is updated.
    """
    async with conn.transaction():
        # TODO: optimize this to use a single query
        for user in users:
            await UserDB.upsert(
                conn,
                telegram_id=user.telegram_id,
                first_name=user.first_name,
                last_name=user.last_name,
                meta=user.meta,
                api_keys=user.api_keys,
            )
    return Status(status="ok", message=f"Created {len(users)} users successfully.")


@app.get("/users", tags=[USER_TAG])
async def _get_user(
    telegram_id: str | None = Query(None, description="Telegram user ID in the format 'user{telegram_id}'"),
    conn=Depends(get_conn),
) -> User | None:
    """
    Retrieve a user by their Telegram ID.

    Returns the user's profile information including their selected model and chat preferences.
    """
    if telegram_id is None:
        raise

    data = await get_user(conn, telegram_id=telegram_id)
    return data


CHAT_TAG = "chat"


@app.get(
    "/chats/{chat_id}/ask",
    tags=[CHAT_TAG],
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "API key for the specified model is missing"},
    },
)
async def _ask_question(
    chat_id: int,
    question: str,
    user_id: int,
    model: str,
    conn=Depends(get_conn),
    session=Depends(get_client),
) -> AskQuestion:
    """
    Ask a question about a chat's content using RAG (Retrieval-Augmented Generation).

    Searches for relevant messages in the chat history and uses the specified AI model
    to generate an answer based on the retrieved context. The question and response
    are saved for feedback tracking.
    """

    chat_username = await UserDB.get_telegram_chat_username(conn, chat_id, user_id)

    api_keys = await UserDB.get_api_keys(conn, user_id=user_id)
    chat_session = ChatSession.get_session(model_name=model, api_keys=api_keys)
    if not api_keys:
        raise APIException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            reason=f"API key for model {model!r} not found for user {user_id!r}",
            error_code=ErrorCode.missing_api_key,
        )

    resp, debug = await chat_session.ask_rag(
        conn=conn, chat_id=chat_id, session=session, question=question, user=chat_username, limit=3
    )
    meta = await conn.fetchval("SELECT meta FROM general.users WHERE id = $1", user_id)
    _question = QuestionDB(
        question=question,
        user_id=user_id,
        meta=meta,
    )
    question_id = await _question.save(conn)

    results: list[EmbeddingResult] = [
        {
            "summary": result["summary"],
            "title": result["title"],
            "day": result["day"],
            "distance": result["distance"],
        }
        for result in debug["results"]
    ]
    return AskQuestion(response=resp, results=results, question_id=question_id)


@app.post(
    "/chats/upload",
    tags=[CHAT_TAG],
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Invalid chat_type provided"},
        status.HTTP_409_CONFLICT: {"description": "File with the same name was already uploaded by this user"},
        status.HTTP_411_LENGTH_REQUIRED: {"description": "Missing X-Content-Length header"},
    },
)
async def _upload_chat(
    request: Request,
    user_id: int,
    filename: str,
    mime_type: str,
    chat_type: ChatType,
    conn=Depends(get_conn),
    s3=Depends(get_s3_client),
    session=Depends(get_client),
) -> ChatUpload:
    """
    Upload a chat export file for processing.

    The file is streamed directly to S3 storage and registered in the database.
    Requires the `X-Content-Length` header to be set with the file size.
    """
    # TODO: Fix that later and use Content-Length
    _content_length = request.headers.get("X-Content-Length")
    if _content_length is None:
        raise APIException(
            status_code=status.HTTP_411_LENGTH_REQUIRED,
            reason="Missing X-Content-Length header",
            error_code=ErrorCode.invalid_headers,
        )
    content_length = int(_content_length)
    get_hash, update_hash = compute_md5()

    file_path = f"{user_id}/{filename}"

    _chat_type = await conn.fetchrow(
        """
                                       SELECT id, name
                                       FROM general.chat_types
                                       WHERE internal_code = $1
                                       """,
        chat_type,
    )
    if _chat_type is None:
        raise APIException(
            status_code=status.HTTP_400_BAD_REQUEST,
            reason=f"Invalid chat type {chat_type!r}",
            error_code=ErrorCode.invalid_token,
        )
    chat_type_id = _chat_type["id"]
    bucket = env.CHAT_UPLOADS_S3_BUCKET
    if not bucket:
        raise ValueError("CHAT_UPLOADS_S3_BUCKET must be set")
    async with conn.transaction():
        await s3_file_upload(
            s3,
            session,
            data=request.stream(),
            bucket=bucket,
            key=file_path,
            on_chunk_received=update_hash,
            content_length=content_length,
            # content_length=int(content_length) if content_length is not None else None
        )
        try:
            file_hash = get_hash()
            chat_upload = ChatUploadDB(
                user_id=user_id,
                filename=filename,
                md5=file_hash,
                mime_type=mime_type,
                file_path=file_path,
                file_size=content_length,
                chat_type_id=chat_type_id,
            )
            try:
                file_id = await chat_upload.save(conn)
            except asyncpg.UniqueViolationError:
                raise APIException(
                    status_code=status.HTTP_409_CONFLICT,
                    reason=f"File {filename!r} already uploaded by user {user_id!r}",
                    error_code=ErrorCode.already_exists,
                )
        except Exception as e:
            # TODO: Remove the uploaded file from S3 if database operation fails
            try:
                await s3_delete_object(s3=s3, client=session, bucket=bucket, key=file_path)
            except Exception as e2:
                logs.warning(f"Failed to delete S3 object {file_path!r}")
                raise e2 from e
            raise e
    logs.info(f"Uploaded file {filename!r} for user {user_id} to {file_path!r}")

    return ChatUpload(
        file_id=file_id,
        filename=filename,
        chat_type=_chat_type["name"],
        created_at=chat_upload.created_at,
        file_path=file_path,
        processed_at=chat_upload.processed_at,
    )


@app.patch("/questions/{question_id}", tags=[CHAT_TAG])
async def _update_question(question_id: int, feedback: str, conn=Depends(get_conn)) -> Status:
    """
    Submit feedback for a previously asked question.

    Used to track user satisfaction with AI-generated answers (e.g., thumbs up/down).
    """
    await QuestionDB.update_feedback(conn, question_id, feedback)
    return Status(status="ok", message=f"Updated question {question_id} with feedback '{feedback}'.")


MODELS: list[AIModel] = [
    # Gemini models
    AIModel(source="gemini", name="gemini-2.5-pro"),
    AIModel(source="gemini", name="gemini-2.5-flash"),
    AIModel(source="gemini", name="gemini-2.5-flash (thinking)"),
    AIModel(source="gemini", name="gemini-2.5-flash-lite-preview-06-17"),
    AIModel(source="gemini", name="gemini-2.5-flash-preview-05-20 (thinking)"),
    # GPT models
    AIModel(source="openai", name="gpt-4.1"),
    AIModel(source="openai", name="gpt-4.1-mini"),
    AIModel(source="openai", name="gpt-4.1-nano"),
    # Mistral models
    AIModel(source="mistral", name="magistral-medium-latest"),
    AIModel(source="mistral", name="mistral-saba-latest"),
    AIModel(source="mistral", name="mistral-large-latest"),
    AIModel(source="mistral", name="ministral-3b-latest"),
    AIModel(source="mistral", name="ministral-8b-latest"),
    AIModel(source="mistral", name="mistral-medium-latest"),
    AIModel(source="mistral", name="open-mistral-nemo"),
    # Grok models
    AIModel(source="grok", name="grok-3-mini"),
]


@app.get("/models", tags=[CHAT_TAG])
async def _get_models() -> list[AIModel]:
    """
    List all available AI models for question answering.

    Returns models from multiple providers (Gemini, OpenAI, Mistral, Grok).
    """
    return MODELS


@app.exception_handler(APIException)
async def api_exception_handler(_: Request, exc: APIException):
    content: dict[str, Any] = {
        "error_code": exc.error_code,
        "reason": exc.reason,
    }
    if exc.payload:
        content["payload"] = exc.payload

    return JSONResponse(status_code=exc.status_code, headers=exc.headers, content=content)


@app.exception_handler(status.HTTP_500_INTERNAL_SERVER_ERROR)
async def exception_500_handler(_: Request, __: Exception):
    return JSONResponse(
        content={"message": "Internal server error"},
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
