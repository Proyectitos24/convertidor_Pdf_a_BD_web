"""
Pestaña "Convertir": subida de PDFs y generación de .db.

Tras generar cada .db se lee su grupo operativo desde la tabla Metadata
(fuente de verdad) y se guarda también en Supabase. La publicación de
cada .db (services/conversion_publication.py) evita duplicados dentro de
la tienda autenticada: reserva atómica en Supabase por (tienda, nombre),
comparación por huella lógica con los archivos disponibles, y
compensación de R2 solo si se confirma que la fila no existe (ver
services/upload_compensation.py).

Conversiones repetidas mientras una operación está en curso:

  - el botón se deshabilita mediante un callback on_click que marca la
    operación como en curso ANTES del rerun (CONVERT_EN_CURSO_KEY);
  - un bloqueo por tienda a nivel de proceso impide dos operaciones
    simultáneas de la misma tienda en este servidor (p.ej. dos
    pestañas), sin depender de session_state;
  - entre servidores, la garantía real es la reserva atómica en
    Supabase (sql/004_evitar_conversiones_duplicadas.sql).

El estado se restaura siempre en un finally (también tras errores o si
Streamlit interrumpe el script), y el resultado de la última operación
se conserva en session_state para mostrarlo tras el rerun.
"""

import threading
from datetime import datetime, timezone

import streamlit as st

from services.conversion_publication import construir_resultado, publicar_db_generados
from services.conversion_service import convert_uploaded_files
from services.time_utils import next_midnight_europe_madrid

CONVERT_EN_CURSO_KEY = "convert_en_curso"
CONVERT_RESULTADO_KEY = "convert_resultado"

_bloqueos_guard = threading.Lock()
_bloqueos_por_tienda = {}


def _bloqueo_de_tienda(store_id):
    with _bloqueos_guard:
        return _bloqueos_por_tienda.setdefault(store_id, threading.Lock())


def _marcar_en_curso():
    st.session_state[CONVERT_EN_CURSO_KEY] = True


def _ejecutar_operacion(store, uploaded_files):
    """Convierte y publica. Devuelve el resultado combinado; no lanza Exception."""
    try:
        generated_files, resumen = convert_uploaded_files(uploaded_files)
        if not generated_files:
            return construir_resultado(resumen, [])

        created_at = datetime.now(timezone.utc)
        expires_at = next_midnight_europe_madrid()
        resultados = publicar_db_generados(store, generated_files, created_at, expires_at)
        return construir_resultado(resumen, resultados)
    except Exception as e:
        return _resultado_simple("error", f"La operación falló y no se completó: {e}")


def _resultado_simple(nivel, mensaje):
    return {"nivel": nivel, "mensaje": mensaje, "conteo": {}, "conversion": [], "publicacion": []}


def _procesar_solicitud(store, uploaded_files):
    if not uploaded_files:
        return _resultado_simple("warning", "Primero sube al menos un PDF.")

    bloqueo = _bloqueo_de_tienda(store["id"])
    if not bloqueo.acquire(blocking=False):
        return _resultado_simple(
            "warning", "Ya hay una conversión en curso para esta tienda. Espera a que termine."
        )

    try:
        with st.spinner("Convirtiendo y guardando archivos..."):
            return _ejecutar_operacion(store, uploaded_files)
    finally:
        bloqueo.release()


def _render_resultado(resultado):
    nivel = resultado.get("nivel")
    mostrar = {"success": st.success, "warning": st.warning}.get(nivel, st.error)
    mostrar(resultado.get("mensaje", ""))

    # El resultado habitual solo necesita el resumen. Conservamos visibles
    # los problemas por archivo sin mostrar una tabla técnica de actividad.
    for r in resultado.get("conversion", []) + resultado.get("publicacion", []):
        if r.get("estado") == "error":
            st.error(f"{r['archivo']}: {r['detalle']}")
        elif r.get("estado") == "conflicto":
            st.warning(f"{r['archivo']}: {r['detalle']}")
        for aviso in r.get("avisos", []):
            st.warning(f"{r['archivo']}: {aviso}")


def render_convert_tab():
    store = st.session_state.selected_store
    en_curso = bool(st.session_state.get(CONVERT_EN_CURSO_KEY))

    st.subheader("Convertir PDFs")

    uploaded_files = st.file_uploader(
        "Selecciona uno o varios PDF",
        type=["pdf"],
        accept_multiple_files=True,
        key="pdf_uploader",
        disabled=en_curso,
    )

    st.button(
        "Convertir y guardar 24h",
        type="primary",
        use_container_width=True,
        disabled=en_curso,
        on_click=_marcar_en_curso,
    )

    if en_curso:
        try:
            resultado = _procesar_solicitud(store, uploaded_files)
            st.session_state[CONVERT_RESULTADO_KEY] = resultado
            st.session_state.last_summary = resultado.get("conversion", [])
            st.session_state.flash_message = ""
        finally:
            st.session_state[CONVERT_EN_CURSO_KEY] = False
        st.rerun()

    resultado = st.session_state.get(CONVERT_RESULTADO_KEY)
    if resultado:
        _render_resultado(resultado)
