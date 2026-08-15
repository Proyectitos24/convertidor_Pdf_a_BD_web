"""
CRUD en Supabase para public.shared_files (pestaña "Compartir APK/PDF").
Calcado deliberadamente del patrón de services/store_db.py para
converted_files, pero en un módulo separado: no se toca store_db.py
para esto, ni se reutiliza converted_files.

Toda lectura filtra siempre por store_id: cada archivo compartido
pertenece exclusivamente a la tienda en cuya sesión se subió, y ninguna
consulta de aquí puede devolver filas de otra tienda.
"""

from datetime import datetime, timezone

from services.supabase_service import get_admin_client


def insert_shared_file(
    store_id: str,
    file_kind: str,
    original_file_name: str,
    object_key: str,
    content_type: str,
    size_bytes: int,
    created_at: datetime,
    expires_at: datetime,
):
    payload = {
        "store_id": store_id,
        "file_kind": file_kind,
        "original_file_name": original_file_name,
        "object_key": object_key,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "status": "ready",
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }

    response = get_admin_client().table("shared_files").insert(payload).execute()
    return response.data[0]


def existe_shared_file(store_id: str, object_key: str) -> bool:
    """
    True si ya existe una fila de shared_files con este store_id y
    object_key — p.ej. porque el INSERT de insert_shared_file() sí se
    confirmó en Supabase y solo se perdió la respuesta (timeout, corte
    de red) antes de que el cliente la recibiera. False si no existe
    ninguna fila así.

    Usada exclusivamente por services.upload_compensation antes de
    decidir si borrar el objeto de R2 tras un INSERT que lanzó una
    excepción: si esta función a su vez lanza una excepción, el
    llamador debe tratarlo como resultado ambiguo (no como False), no
    se captura aquí a propósito.
    """
    response = (
        get_admin_client()
        .table("shared_files")
        .select("id")
        .eq("store_id", store_id)
        .eq("object_key", object_key)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def mark_expired_shared_files(store_id: str):
    now_iso = datetime.now(timezone.utc).isoformat()

    (
        get_admin_client()
        .table("shared_files")
        .update({"status": "expired"})
        .eq("store_id", store_id)
        .eq("status", "ready")
        .lte("expires_at", now_iso)
        .execute()
    )


def list_ready_shared_files(store_id: str):
    now_iso = datetime.now(timezone.utc).isoformat()

    response = (
        get_admin_client()
        .table("shared_files")
        .select(
            "id, file_kind, original_file_name, object_key, content_type, "
            "size_bytes, created_at, expires_at, downloaded_at"
        )
        .eq("store_id", store_id)
        .eq("status", "ready")
        .gt("expires_at", now_iso)
        .order("created_at", desc=True)
        .execute()
    )
    return response.data or []


def mark_shared_file_downloaded(file_id: str):
    now_iso = datetime.now(timezone.utc).isoformat()

    (
        get_admin_client()
        .table("shared_files")
        .update({"downloaded_at": now_iso})
        .eq("id", file_id)
        .execute()
    )


def get_shared_files_for_deletion(store_id: str, file_ids):
    """
    Igual que store_db.get_converted_files_for_deletion: vuelve a
    filtrar por store_id contra Supabase antes de borrar nada. Un id
    que no pertenezca a store_id simplemente no aparece en el resultado.
    """
    file_ids = list(file_ids)
    if not file_ids:
        return []

    response = (
        get_admin_client()
        .table("shared_files")
        .select("id, store_id, object_key, original_file_name")
        .eq("store_id", store_id)
        .in_("id", file_ids)
        .execute()
    )
    return response.data or []


def mark_shared_file_deleted(file_id: str):
    now_iso = datetime.now(timezone.utc).isoformat()

    (
        get_admin_client()
        .table("shared_files")
        .update({"status": "deleted", "deleted_at": now_iso})
        .eq("id", file_id)
        .execute()
    )


def get_expired_shared_files_pending_purge(store_id: str):
    now_iso = datetime.now(timezone.utc).isoformat()

    response = (
        get_admin_client()
        .table("shared_files")
        .select("id, object_key")
        .eq("store_id", store_id)
        .eq("status", "expired")
        .is_("deleted_at", "null")
        .lte("expires_at", now_iso)
        .execute()
    )
    return response.data or []


def mark_shared_file_physically_deleted(file_id: str):
    now_iso = datetime.now(timezone.utc).isoformat()

    (
        get_admin_client()
        .table("shared_files")
        .update({"deleted_at": now_iso})
        .eq("id", file_id)
        .execute()
    )
