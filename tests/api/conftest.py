import os

import pytest
from fastapi.testclient import TestClient

from ..data.db import gen_user
from ..utils import clean_pg_fn

os.environ["CHAT_UPLOADS_S3_BUCKET"] = os.getenv("CHAT_UPLOADS_S3_BUCKET", "test-bucket")


def delete_bucket_and_contents(minio_client, bucket_name):
    """Delete a bucket and all its contents"""
    if not minio_client.bucket_exists(bucket_name):
        return

    # List and delete all objects in the bucket
    objects = minio_client.list_objects(bucket_name, recursive=True)
    for obj in objects:
        minio_client.remove_object(bucket_name, obj.object_name)

    # Now remove the empty bucket
    minio_client.remove_bucket(bucket_name)


@pytest.fixture(scope="session")
def client():
    from polarsen.api.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="session", autouse=True)
def setup_minio(minio_client):
    bucket = os.environ["CHAT_UPLOADS_S3_BUCKET"]
    if not minio_client.bucket_exists(bucket):
        minio_client.make_bucket(bucket)
        return
    objects = minio_client.list_objects(bucket, recursive=True)
    for obj in objects:
        minio_client.remove_object(bucket, obj.object_name)


@pytest.fixture(scope="function", autouse=True)
def clean_pg(engine):
    clean_pg_fn(engine)
    yield


USERS = [gen_user()]
