from datetime import datetime, timezone

from services.supabase_service import get_admin_client


def list_active_stores():
    response = (
        get_admin_client()
        .table("stores")
        .select("id, code, name, active")
        .eq("active", True)
        .order("code")
        .execute()
    )
    return response.data or []


def get_store_by_code(store_code: str):
    response = (
        get_admin_client()
        .table("stores")
        .select("id, code, name, active")
        .eq("code", store_code)
        .eq("active", True)
        .maybe_single()
        .execute()
    )
    return response.data


def insert_converted_file(
    store_id: str,
    original_pdf_name: str,
    db_file_name: str,
    object_key: str,
    size_bytes: int,
    created_at: datetime,
    expires_at: datetime,
):
    payload = {
        "store_id": store_id,
        "original_pdf_name": original_pdf_name,
        "db_file_name": db_file_name,
        "object_key": object_key,
        "size_bytes": size_bytes,
        "status": "ready",
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }

    response = (
        get_admin_client()
        .table("converted_files")
        .insert(payload)
        .execute()
    )

    return response.data[0]


def mark_expired_files(store_id: str):
    now_iso = datetime.now(timezone.utc).isoformat()

    (
        get_admin_client()
        .table("converted_files")
        .update({"status": "expired"})
        .eq("store_id", store_id)
        .eq("status", "ready")
        .lte("expires_at", now_iso)
        .execute()
    )


def list_ready_files(store_id: str):
    now_iso = datetime.now(timezone.utc).isoformat()

    response = (
        get_admin_client()
        .table("converted_files")
        .select("id, original_pdf_name, db_file_name, object_key, size_bytes, created_at, expires_at")
        .eq("store_id", store_id)
        .eq("status", "ready")
        .gt("expires_at", now_iso)
        .order("created_at", desc=True)
        .execute()
    )

    return response.data or []