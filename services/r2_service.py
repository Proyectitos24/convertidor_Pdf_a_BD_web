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


def upload_bytes(object_key: str, data: bytes, content_type: str):
    """Sube bytes ya materializados en memoria con un Content-Type explícito."""
    get_r2_client().put_object(
        Bucket=get_bucket_name(),
        Key=object_key,
        Body=data,
        ContentType=content_type,
    )


def upload_fileobj(object_key: str, fileobj, content_type: str):
    """
    Sube un objeto file-like (p.ej. UploadedFile de Streamlit) directamente
    como Body, sin materializar una copia adicional completa en memoria
    vía getvalue()/read(). El llamador es responsable de haber validado
    el contenido y de dejar el cursor posicionado donde deba empezar la
    lectura (normalmente en 0) antes de llamar a esta función.
    """
    get_r2_client().put_object(
        Bucket=get_bucket_name(),
        Key=object_key,
        Body=fileobj,
        ContentType=content_type,
    )


def delete_object(object_key: str):
    """
    Borra un objeto de R2. Igual que el resto de operaciones S3-compatibles,
    borrar una clave que ya no existe no lanza error (comportamiento propio
    de la API DeleteObject de S3/R2): la operación es idempotente por
    diseño del propio servicio, no por lógica añadida aquí.
    """
    get_r2_client().delete_object(
        Bucket=get_bucket_name(),
        Key=object_key,
    )


def upload_db_bytes(object_key: str, data: bytes):
    upload_bytes(object_key, data, content_type="application/octet-stream")


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

def download_db_bytes(object_key: str) -> bytes:
    response = get_r2_client().get_object(
        Bucket=get_bucket_name(),
        Key=object_key,
    )
    return response["Body"].read()