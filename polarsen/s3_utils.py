import contextlib
from collections import namedtuple
from contextlib import asynccontextmanager
from typing import AsyncIterator, TypedDict, Callable

import niquests
from niquests import HTTPError

try:
    import botocore.client
    from botocore.exceptions import ClientError
    import botocore.session
except ImportError as e:
    raise ImportError("botocore is required for S3 operations") from e

from polarsen.utils import get_stream_chunk
from polarsen import env
from polarsen.logs import logs

__all__ = (
    "s3_file_upload",
    "s3_delete_object",
    "S3MultipartUpload",
    "s3_put_object",
    "s3_get_object",
    "UploadPart",
    "get_s3_client",
)


@contextlib.contextmanager
def get_s3_client():
    session = botocore.session.Session()

    if not env.S3_ACCESS_KEY_ID:
        raise ValueError("S3_ACCESS_KEY_ID is not set in the environment variables.")
    if not env.S3_SECRET_ACCESS_KEY:
        raise ValueError("S3_SECRET_ACCESS_KEY is not set in the environment variables.")

    s3_client = session.create_client(
        "s3",
        endpoint_url=env.S3_ENDPOINT,
        region_name=env.S3_REGION,
        aws_secret_access_key=env.S3_SECRET_ACCESS_KEY,
        aws_access_key_id=env.S3_ACCESS_KEY_ID,
    )
    yield s3_client


S3MultipartUpload = namedtuple(
    "S3MultipartUpload", ["fetch_complete", "upload_part", "generate_presigned_url", "fetch_abort"]
)


class UploadPart(TypedDict):
    PartNumber: int
    ETag: str | None


@asynccontextmanager
async def s3_multipart_upload(
    s3: botocore.client.BaseClient, client: niquests.AsyncSession, bucket: str, key: str, *, expires_in: int = 3600
):
    """Async context manager for S3 multipart upload with automatic cleanup."""
    upload_id: str | None = None
    _part_number: int = 1
    _parts: list[UploadPart] = []
    _has_been_aborted = False

    async def fetch_complete():
        if upload_id is None:
            raise ValueError("Upload ID is not set")
        complete_url = _generate_presigned_url("complete_multipart_upload", UploadId=upload_id)
        # Create XML payload for completing multipart upload
        parts_xml = "".join(
            f"<Part><PartNumber>{part['PartNumber']}</PartNumber><ETag>{part['ETag']}</ETag></Part>" for part in _parts
        )
        xml_payload = f"<CompleteMultipartUpload>{parts_xml}</CompleteMultipartUpload>"

        complete_resp = await client.post(complete_url, data=xml_payload, headers={"Content-Type": "application/xml"})
        complete_resp.raise_for_status()
        return complete_resp

    async def fetch_abort():
        nonlocal _has_been_aborted
        if upload_id is None:
            raise ValueError("Upload ID is not set")
        abort_url = _generate_presigned_url("abort_multipart_upload", UploadId=upload_id)
        abort_resp = await client.delete(abort_url)
        abort_resp.raise_for_status()
        _has_been_aborted = True
        return abort_resp

    async def upload_part(data: bytes) -> UploadPart:
        nonlocal _part_number, _parts
        if upload_id is None:
            raise ValueError("Upload ID is not set")
        presigned_url = _generate_presigned_url("upload_part", UploadId=upload_id, PartNumber=_part_number)
        # Upload part using niquests
        upload_resp = await client.put(
            presigned_url,
            data=data,
        )
        upload_resp.raise_for_status()

        # Extract ETag from response headers
        etag = upload_resp.headers.get("ETag")
        _part: UploadPart = {"PartNumber": _part_number, "ETag": etag}
        _parts.append(_part)
        _part_number += 1
        return _part

    def _generate_presigned_url(method: str, **params):
        return s3.generate_presigned_url(
            ClientMethod=method, Params={"Bucket": bucket, "Key": key, **params}, ExpiresIn=expires_in
        )

    try:
        response = s3.create_multipart_upload(Bucket=bucket, Key=key)
        upload_id = response["UploadId"]
        yield S3MultipartUpload(
            fetch_complete=fetch_complete,
            upload_part=upload_part,
            fetch_abort=fetch_abort,
            generate_presigned_url=_generate_presigned_url,
        )
    except ClientError as e:
        raise Exception(f"Failed to initiate multipart upload: {e}")
    except Exception as e:
        if not _has_been_aborted and upload_id is not None:
            await fetch_abort()
        raise e
    else:
        if not _has_been_aborted and upload_id is not None:
            await fetch_complete()


async def s3_file_upload(
    s3: botocore.client.BaseClient,
    client: niquests.AsyncSession,
    bucket: str,
    key: str,
    data: AsyncIterator[bytes],
    # 5MB minimum for S3 parts
    min_part_size: int = 5 * 1024 * 1024,
    on_chunk_received: Callable[[bytes], None] | None = None,
    content_length: int | None = None,
) -> None:
    """
    Upload a file to S3 using multipart upload from an async byte stream.
    """
    if content_length is not None and content_length < min_part_size:
        logs.debug("Content length is less than min_part_size, using single PUT operation")
        # Consume AsyncIterator
        _data = b""
        async for chunk in data:
            _data += chunk
            if on_chunk_received:
                on_chunk_received(chunk)
        await s3_put_object(s3, client, bucket=bucket, key=key, data=_data)
        return

    async with s3_multipart_upload(s3, client, bucket=bucket, key=key) as mpart:
        async for chunk in get_stream_chunk(data, min_part_size=min_part_size):
            if on_chunk_received:
                on_chunk_received(chunk)
            if len(chunk) < min_part_size:
                await mpart.fetch_abort()
                await s3_put_object(s3, client, bucket=bucket, key=key, data=chunk)
                break
            await mpart.upload_part(chunk)


async def s3_delete_object(
    s3: botocore.client.BaseClient, client: niquests.AsyncSession, bucket: str, key: str
) -> niquests.Response:
    """Delete an object from S3."""
    url = s3.generate_presigned_url(
        ClientMethod="delete_object",
        Params={
            "Bucket": bucket,
            "Key": key,
        },
    )
    resp = await client.delete(url)
    resp.raise_for_status()
    return resp


async def s3_put_object(
    s3: botocore.client.BaseClient, client: niquests.AsyncSession, bucket: str, key: str, data: bytes
) -> niquests.Response:
    """Upload an object to S3."""
    url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": bucket,
            "Key": key,
        },
    )
    resp = await client.put(url, data=data)
    try:
        resp.raise_for_status()
    except HTTPError:
        logs.error(f"S3 put_object failed: {resp.text}")
        raise
    return resp


async def s3_get_object(
    s3: botocore.client.BaseClient, client: niquests.AsyncSession, bucket: str, key: str
) -> bytes | None:
    """Download an object from S3."""
    url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": bucket,
            "Key": key,
        },
    )
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.content
