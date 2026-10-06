"""
Publicación (R2 + converted_files) de los .db generados en una
operación, sin crear duplicados dentro de la tienda autenticada.

Para cada .db, siempre con el store_id de la sesión del servidor:

  1. Reserva atómica en Supabase de (store_id, db_file_name)
     (sql/004_evitar_conversiones_duplicadas.sql). Si otra operación
     tiene la reserva -> error "en curso en otra operación".
  2. Con la reserva en mano, se consultan SOLO los registros
     disponibles de esa tienda con ese nombre:
       - alguno con la misma huella lógica -> "ya_disponible" (no se
         sube ni se registra nada);
       - alguno que no se pueda verificar -> "error" (nunca se trata
         como "no existe");
       - todos con huella distinta -> "conflicto" (no se sobrescribe ni
         se crea otra copia);
       - ninguno -> se sube a R2 y se publica.
     Los registros antiguos sin huella se verifican descargando ese
     candidato concreto y calculando su huella en solo lectura.
  3. publicar_convertido_reservado inserta la fila en la misma
     transacción en que comprueba que la reserva sigue siendo suya.
     Va envuelto en ejecutar_con_compensacion_r2: si falla o lo
     rechaza, solo se borra el objeto de R2 si se confirma que la fila
     no existe; el error original nunca se oculta.
  4. Se libera la reserva (si ya se publicó, no queda nada que borrar;
     si la liberación falla, la reserva caduca sola).
"""

from services.db_fingerprint import es_huella_actual, huella_logica_de_bytes
from services.db_metadata_reader import leer_grupo_de_db_bytes
from services.r2_service import build_object_key, download_db_bytes, upload_db_bytes
from services.store_db import (
    existe_converted_file,
    liberar_reserva,
    listar_disponibles_por_nombre,
    publicar_convertido_reservado,
    reservar_publicacion,
)
from services.upload_compensation import ejecutar_con_compensacion_r2

LEASE_SEGUNDOS = 300

GUARDADO = "guardado"
YA_DISPONIBLE = "ya_disponible"
CONFLICTO = "conflicto"
ERROR = "error"


class PublicacionRechazada(Exception):
    """publicar_convertido_reservado no insertó la fila (respuesta lógica, no ambigua)."""

    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.resultado = (respuesta or {}).get("resultado")
        super().__init__(f"Publicación rechazada por Supabase: {self.resultado!r}")


def _resultado(item, estado, detalle, avisos=None):
    return {
        "archivo": item["name"],
        "source_pdf": item.get("source_pdf"),
        "estado": estado,
        "detalle": detalle,
        "avisos": list(avisos or []),
    }


def _clasificar_candidatos(candidatos, huella):
    iguales, distintos, errores = [], [], []
    for candidato in candidatos:
        huella_candidato = candidato.get("content_fingerprint")
        if not es_huella_actual(huella_candidato):
            try:
                huella_candidato = huella_logica_de_bytes(download_db_bytes(candidato["object_key"]))
            except Exception as e:
                errores.append((candidato, e))
                continue
        (iguales if huella_candidato == huella else distintos).append(candidato)
    return iguales, distintos, errores


def _publicar_con_reserva(store, item, token, created_at, expires_at):
    store_id = store["id"]
    nombre = item["name"]
    huella = item["fingerprint"]

    try:
        candidatos = listar_disponibles_por_nombre(store_id, nombre)
    except Exception as e:
        return _resultado(item, ERROR, f"No se pudo comprobar si ya existía: {e}")

    avisos = []
    if len(candidatos) > 1:
        avisos.append(
            f"Existen {len(candidatos)} registros disponibles con este nombre "
            f"(duplicados anteriores); no se han modificado"
        )

    if candidatos:
        iguales, distintos, errores = _clasificar_candidatos(candidatos, huella)
        for candidato, e in errores:
            avisos.append(f"No se pudo verificar el registro existente {candidato.get('id')}: {e}")

        if iguales:
            return _resultado(item, YA_DISPONIBLE, "Ya estaba disponible", avisos)
        if errores:
            return _resultado(
                item,
                ERROR,
                "Existe un registro con este nombre que no se pudo verificar; no se guarda",
                avisos,
            )
        return _resultado(
            item,
            CONFLICTO,
            "Ya existe un archivo disponible con este nombre pero con datos distintos; "
            "no se sobrescribe ni se crea otra copia",
            avisos,
        )

    data = item["data"]
    object_key = build_object_key(store["code"], nombre)
    grupo = leer_grupo_de_db_bytes(data)

    try:
        upload_db_bytes(object_key, data)
    except Exception as e:
        return _resultado(item, ERROR, f"No se pudo subir a R2: {e}", avisos)

    def insertar():
        respuesta = publicar_convertido_reservado(
            store_id=store_id,
            token=token,
            original_pdf_name=item["source_pdf"],
            db_file_name=nombre,
            object_key=object_key,
            size_bytes=len(data),
            created_at=created_at,
            expires_at=expires_at,
            grupo=grupo,
            content_fingerprint=huella,
        )
        if respuesta.get("resultado") != "publicado":
            raise PublicacionRechazada(respuesta)
        return respuesta

    try:
        ejecutar_con_compensacion_r2(
            object_key,
            insertar,
            lambda: existe_converted_file(store_id, object_key),
        )
    except PublicacionRechazada as e:
        if e.resultado == "ya_existe":
            return _resultado(
                item,
                CONFLICTO,
                "Apareció otro registro disponible con este nombre durante la operación; no se guarda",
                avisos,
            )
        return _resultado(item, ERROR, f"No se guardó: {e}", avisos)
    except Exception as e:
        return _resultado(item, ERROR, f"No se pudo registrar en Supabase: {e}", avisos)

    return _resultado(item, GUARDADO, "Guardado", avisos)


def publicar_db_generado(store, item, created_at, expires_at):
    store_id = store["id"]
    nombre = item["name"]

    try:
        reserva = reservar_publicacion(store_id, nombre, item["fingerprint"], LEASE_SEGUNDOS)
    except Exception as e:
        return _resultado(item, ERROR, f"No se pudo reservar la publicación: {e}")

    if reserva.get("resultado") == "ocupado":
        return _resultado(
            item,
            ERROR,
            "Otra operación está guardando este mismo archivo ahora mismo; "
            "revisa «Archivos 24h» o vuelve a intentarlo en unos minutos",
        )

    token = reserva.get("token")
    if reserva.get("resultado") != "reservado" or not token:
        return _resultado(item, ERROR, f"Respuesta inesperada al reservar: {reserva!r}")

    resultado = None
    try:
        try:
            resultado = _publicar_con_reserva(store, item, token, created_at, expires_at)
        except Exception as e:
            resultado = _resultado(item, ERROR, f"Error inesperado al guardar: {e}")
    finally:
        # También si Streamlit interrumpe el script (RerunException /
        # StopException derivan de BaseException, no de Exception).
        try:
            liberar_reserva(store_id, nombre, token)
        except Exception as e:
            if resultado is not None:
                resultado["avisos"].append(
                    f"No se pudo liberar la reserva ({e}); caducará sola en {LEASE_SEGUNDOS // 60} min"
                )

    return resultado


def publicar_db_generados(store, generated_files, created_at, expires_at):
    return [publicar_db_generado(store, item, created_at, expires_at) for item in generated_files]


def construir_resultado(resumen_conversion, resultados_publicacion):
    """
    Combina el resumen de conversión (por PDF) y el de publicación (por
    .db) en un único resultado con conteos separados. Solo es "success"
    si no hubo errores, conflictos ni PDFs omitidos.
    """
    conteo = {
        "guardados": sum(1 for r in resultados_publicacion if r["estado"] == GUARDADO),
        "ya_disponibles": sum(1 for r in resultados_publicacion if r["estado"] == YA_DISPONIBLE),
        "duplicados_omitidos": sum(1 for r in resumen_conversion if r["estado"] == "duplicado"),
        "conflictos": (
            sum(1 for r in resultados_publicacion if r["estado"] == CONFLICTO)
            + sum(1 for r in resumen_conversion if r["estado"] == "conflicto")
        ),
        "errores": (
            sum(1 for r in resultados_publicacion if r["estado"] == ERROR)
            + sum(1 for r in resumen_conversion if r["estado"] == "error")
        ),
        "omitidos": sum(1 for r in resumen_conversion if r["estado"] == "omitido"),
    }

    partes = [f"Guardados: {conteo['guardados']}"]
    if conteo["ya_disponibles"]:
        partes.append(f"ya disponibles: {conteo['ya_disponibles']}")
    if conteo["duplicados_omitidos"]:
        partes.append(f"duplicados omitidos: {conteo['duplicados_omitidos']}")
    if conteo["conflictos"]:
        partes.append(f"conflictos: {conteo['conflictos']}")
    if conteo["omitidos"]:
        partes.append(f"PDF no convertidos: {conteo['omitidos']}")
    if conteo["errores"]:
        partes.append(f"errores: {conteo['errores']}")
    mensaje = ", ".join(partes) + "."

    if conteo["errores"]:
        nivel = "error"
    elif conteo["conflictos"] or conteo["omitidos"]:
        nivel = "warning"
    else:
        nivel = "success"
        if conteo["guardados"]:
            mensaje += " Los archivos estarán visibles hasta las 00:00."

    return {
        "nivel": nivel,
        "mensaje": mensaje,
        "conteo": conteo,
        "conversion": list(resumen_conversion),
        "publicacion": list(resultados_publicacion),
    }
