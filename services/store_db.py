from datetime import datetime, timezone

from services.supabase_service import get_admin_client
from services.grupo_packinglist import SIN_CLASIFICAR


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
    grupo: str = SIN_CLASIFICAR,
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
        "grupo": grupo or SIN_CLASIFICAR,
    }

    response = (
        get_admin_client()
        .table("converted_files")
        .insert(payload)
        .execute()
    )

    return response.data[0]


def existe_converted_file(store_id: str, object_key: str) -> bool:
    """
    True si ya existe una fila de converted_files con este store_id y
    object_key — p.ej. porque el INSERT de insert_converted_file() sí
    se confirmó en Supabase y solo se perdió la respuesta (timeout,
    corte de red) antes de que el cliente la recibiera. False si no
    existe ninguna fila así.

    Usada exclusivamente por services.upload_compensation antes de
    decidir si borrar el objeto de R2 tras un INSERT que lanzó una
    excepción: si esta función a su vez lanza una excepción, el
    llamador debe tratarlo como resultado ambiguo (no como False), no
    se captura aquí a propósito.
    """
    response = (
        get_admin_client()
        .table("converted_files")
        .select("id")
        .eq("store_id", store_id)
        .eq("object_key", object_key)
        .limit(1)
        .execute()
    )
    return bool(response.data)


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

    # Orden explícito y determinista: created_at descendente, con id
    # descendente como desempate estable cuando dos filas comparten el
    # mismo created_at (o el mismo valor truncado por el motor). Nunca
    # se ordena por downloaded_at: marcar un archivo como descargado no
    # debe mover su card de sitio en la interfaz.
    response = (
        get_admin_client()
        .table("converted_files")
        .select(
            "id, original_pdf_name, db_file_name, object_key, size_bytes, "
            "created_at, expires_at, downloaded_at, grupo"
        )
        .eq("store_id", store_id)
        .eq("status", "ready")
        .gt("expires_at", now_iso)
        .order("created_at", desc=True)
        .order("id", desc=True)
        .execute()
    )

    filas = response.data or []
    # Defensivo: si algún registro no trajera 'grupo' (p.ej. un entorno
    # todavía sin la migración 002 aplicada), no debe romper la interfaz.
    for fila in filas:
        fila["grupo"] = fila.get("grupo") or SIN_CLASIFICAR

    return filas

def mark_file_downloaded(file_id: str):
    now_iso = datetime.now(timezone.utc).isoformat()

    (
        get_admin_client()
        .table("converted_files")
        .update({"downloaded_at": now_iso})
        .eq("id", file_id)
        .execute()
    )


def get_converted_files_for_deletion(store_id: str, file_ids):
    """
    Vuelve a pedir a Supabase, filtrado SIEMPRE por store_id, las filas
    que se van a borrar. Cualquier id de file_ids que no pertenezca a
    store_id simplemente no aparece en el resultado — es la defensa
    contra acceso cruzado entre tiendas en el momento del borrado, no
    solo en el listado.
    """
    file_ids = list(file_ids)
    if not file_ids:
        return []

    response = (
        get_admin_client()
        .table("converted_files")
        .select("id, store_id, object_key, db_file_name")
        .eq("store_id", store_id)
        .in_("id", file_ids)
        .execute()
    )
    return response.data or []


def mark_file_deleted(file_id: str):
    now_iso = datetime.now(timezone.utc).isoformat()

    (
        get_admin_client()
        .table("converted_files")
        .update({"status": "deleted", "deleted_at": now_iso})
        .eq("id", file_id)
        .execute()
    )


def get_expired_files_pending_purge(store_id: str):
    """
    Filas ya marcadas 'expired' (por mark_expired_files) cuyo objeto de
    R2 todavía no se ha intentado/logrado purgar (deleted_at IS NULL).
    Sirve tanto para el primer intento como para reintentos posteriores.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    response = (
        get_admin_client()
        .table("converted_files")
        .select("id, object_key")
        .eq("store_id", store_id)
        .eq("status", "expired")
        .is_("deleted_at", "null")
        .lte("expires_at", now_iso)
        .execute()
    )
    return response.data or []


def mark_file_physically_deleted(file_id: str):
    """Registra que el objeto de R2 ya se purgó, sin tocar 'status'."""
    now_iso = datetime.now(timezone.utc).isoformat()

    (
        get_admin_client()
        .table("converted_files")
        .update({"deleted_at": now_iso})
        .eq("id", file_id)
        .execute()
    )
