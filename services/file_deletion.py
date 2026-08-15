"""
Lógica de eliminación segura y de purga oportunista, compartida entre
"Archivos 24h" (converted_files) y "Compartir APK/PDF" (shared_files) —
para no duplicar la misma regla de negocio dos veces.

Reglas de seguridad, válidas para ambos dominios:
  - nunca se acepta un object_key venido directamente del navegador: las
    filas a borrar se vuelven a pedir a Supabase filtradas por store_id
    justo antes de borrar nada (el "obtener_filas" que recibe cada
    función es siempre una consulta ya filtrada por tienda);
  - el borrado de R2 se intenta primero; solo si tiene éxito se marca la
    fila como eliminada en Supabase — si R2 falla, la fila NO se marca
    como eliminada, para no mentir sobre el estado real;
  - operación idempotente: volver a pedir el borrado de una fila ya
    eliminada no debe fallar (delete_object no lanza error sobre una
    clave que ya no existe, es el comportamiento estándar de la API
    DeleteObject de S3/R2).
"""

from services.r2_service import delete_object


def eliminar_archivos_seguro(store_id, file_ids, obtener_filas, marcar_eliminada):
    """
    store_id: tienda autenticada de la sesión actual.
    file_ids: ids seleccionados por el usuario en la interfaz.
    obtener_filas(store_id, file_ids) -> filas de Supabase ya filtradas
        por store_id (p.ej. store_db.get_converted_files_for_deletion o
        shared_files_db.get_shared_files_for_deletion).
    marcar_eliminada(file_id) -> marca status='deleted' + deleted_at.

    Devuelve (eliminados, fallidos, ids_no_encontrados):
      - eliminados: filas borradas de R2 y marcadas en Supabase.
      - fallidos: filas cuyo borrado en R2 lanzó una excepción (no se
        tocó Supabase para esas filas).
      - ids_no_encontrados: ids pedidos que no pertenecían a store_id o
        ya no existían — ni intento de borrado ni error, simplemente no
        había nada que hacer con ellos (posible acceso cruzado o doble
        clic sobre una fila ya borrada).
    """
    file_ids = list(file_ids)
    filas = obtener_filas(store_id, file_ids)

    eliminados = []
    fallidos = []

    for fila in filas:
        try:
            delete_object(fila["object_key"])
        except Exception as exc:  # noqa: BLE001 - se reporta, no se oculta
            fallidos.append({**fila, "error": str(exc)})
            continue

        marcar_eliminada(fila["id"])
        eliminados.append(fila)

    ids_encontrados = {fila["id"] for fila in filas}
    ids_no_encontrados = [fid for fid in file_ids if fid not in ids_encontrados]

    return eliminados, fallidos, ids_no_encontrados


def purgar_expirados_seguro(store_id, obtener_pendientes, marcar_purgado):
    """
    Limpieza oportunista: para las filas ya vencidas (obtener_pendientes
    debe devolver solo filas con deleted_at IS NULL, listas para
    reintento), intenta borrar el objeto físico de R2 y, solo si tiene
    éxito, registra deleted_at. Si R2 falla, la fila queda igual que
    estaba — se reintentará la próxima vez que alguien abra la pestaña.

    No cambia la visibilidad del archivo: eso ya lo garantiza el filtro
    de expires_at en las consultas de listado, independientemente de si
    la purga física tuvo éxito.
    """
    pendientes = obtener_pendientes(store_id)

    purgados = []
    fallidos = []

    for fila in pendientes:
        try:
            delete_object(fila["object_key"])
        except Exception as exc:  # noqa: BLE001
            fallidos.append({**fila, "error": str(exc)})
            continue

        marcar_purgado(fila["id"])
        purgados.append(fila)

    return purgados, fallidos
