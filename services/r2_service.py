from datetime import datetime, timezone
from uuid import uuid4

import boto3
import streamlit as st
from botocore.config import Config


@st.cache_resource
def get_r2_client():
    account_id = st.secrets["r2"]["account_id"]

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=st.secrets["r2"]["access_key_id"],
        aws_secret_access_key=st.secrets["r2"]["secret_access_key"],
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def get_bucket_name() -> str:
    return st.secrets["r2"]["bucket"]


def build_object_key(store_code: str, db_file_name: str) -> str:
    now = datetime.now(timezone.utc)
    random_part = uuid4().hex
    return f"stores/{store_code}/{now:%Y/%m/%d}/{random_part}_{db_file_name}"


def upload_db_bytes(object_key: str, data: bytes):
    get_r2_client().put_object(
        Bucket=get_bucket_name(),
        Key=object_key,
        Body=data,
        ContentType="application/octet-stream",
    )


def generate_download_url(object_key: str, download_name: str, expires_in: int = 900) -> str:
    return get_r2_client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": get_bucket_name(),
            "Key": object_key,
            "ResponseContentDisposition": f'attachment; filename="{download_name}"',
        },
        ExpiresIn=expires_in,
    )