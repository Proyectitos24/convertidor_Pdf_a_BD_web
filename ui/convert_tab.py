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

Albaranes AM613A: no traen el grupo operativo, así que cada AM613A
distinto del lote (por SHA-256 de su contenido, no por nombre) muestra
un selector de grupo sin valor inicial; convertir sin elegirlo no está
permitido. La elección se refleja además en AM613A_GRUPOS_KEY para que
sobreviva a los reruns aunque Streamlit descarte el estado del widget.

El estado se restaura siempre en un finally (también tras errores o si
Streamlit interrumpe el script), y el resultado de la última operación
se conserva en session_state para mostrarlo tras el rerun.
"""

import threading
from datetime import datetime, timezone

import streamlit as st

from services.conversion_publication import construir_resultado, publicar_db_generados
from services.conversion_service import am613a_de_subidas, convert_uploaded_files
from services.grupo_packinglist import GRUPO_ETIQUETAS, GRUPO_ORDEN, GRUPOS_VALIDOS
from services.time_utils import next_midnight_europe_madrid

CONVERT_EN_CURSO_KEY = "convert_en_curso"
CONVERT_RESULTADO_KEY = "convert_resultado"
AM613A_GRUPOS_KEY = "convert_am613a_grupos"
FORMATOS_CACHE_KEY = "convert_formatos_cache"

_bloqueos_guard = threading.Lock()
_bloqueos_por_tienda = {}


def _bloqueo_de_tienda(store_id):
    with _bloqueos_guard:
        return _bloqueos_por_tienda.setdefault(store_id, threading.Lock())


def _marcar_en_curso():
    st.session_state[CONVERT_EN_CURSO_KEY] = True


def _clave_selector_grupo(sha):
    return f"am613a_grupo_{sha}"


def _guardar_grupo_am613a(sha):
    grupos = st.session_state.setdefault(AM613A_GRUPOS_KEY, {})
    grupos[sha] = st.session_state.get(_clave_selector_grupo(sha))


def _render_selectores_am613a(uploaded_files, en_curso):
    """Un selector de grupo por AM613A distinto. Devuelve [(sha, nombre, grupo|None)]."""
    cache = st.session_state.setdefault(FORMATOS_CACHE_KEY, {})
    grupos = st.session_state.setdefault(AM613A_GRUPOS_KEY, {})

    elecciones = []
    for sha, nombre in am613a_de_subidas(uploaded_files, cache):
        clave = _clave_selector_grupo(sha)
        if clave not in st.session_state and grupos.get(sha) in GRUPOS_VALIDOS:
            st.session_state[clave] = grupos[sha]
        opciones_extra = {} if clave in st.session_state else {"index": None}
        valor = st.selectbox(
            f"Grupo del albarán AM613A {nombre}",
            options=GRUPO_ORDEN,
            format_func=lambda g: GRUPO_ETIQUETAS.get(g, g),
            placeholder="Elige un grupo",
            key=clave,
            disabled=en_curso,
            on_change=_guardar_grupo_am613a,
            args=(sha,),
            **opciones_extra,
        )
        grupo = valor if valor in GRUPOS_VALIDOS else None
        if grupo is not None:
            grupos[sha] = grupo
        elecciones.append((sha, nombre, grupo))
    return elecciones


def _ejecutar_operacion(store, uploaded_files, grupos_am613a=None):
    """Convierte y publica. Devuelve el resultado combinado; no lanza Exception."""
    try:
        generated_files, resumen = convert_uploaded_files(uploaded_files, grupos_am613a or {})
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


def _procesar_solicitud(store, uploaded_files, elecciones_am613a=()):
    if not uploaded_files:
        return _resultado_simple("warning", "Primero sube al menos un PDF.")

    sin_grupo = [nombre for _sha, nombre, grupo in elecciones_am613a if grupo is None]
    if sin_grupo:
        return _resultado_simple(
            "warning", "Elige el grupo de cada albarán AM613A antes de convertir: " + ", ".join(sin_grupo)
        )
    grupos_am613a = {sha: grupo for sha, _nombre, grupo in elecciones_am613a}

    bloqueo = _bloqueo_de_tienda(store["id"])
    if not bloqueo.acquire(blocking=False):
        return _resultado_simple(
            "warning", "Ya hay una conversión en curso para esta tienda. Espera a que termine."
        )

    try:
        with st.spinner("Convirtiendo y guardando archivos..."):
            return _ejecutar_operacion(store, uploaded_files, grupos_am613a)
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

    elecciones_am613a = _render_selectores_am613a(uploaded_files, en_curso)

    st.button(
        "Convertir y guardar 24h",
        type="primary",
        use_container_width=True,
        disabled=en_curso,
        on_click=_marcar_en_curso,
    )

    if en_curso:
        try:
            resultado = _procesar_solicitud(store, uploaded_files, elecciones_am613a)
            st.session_state[CONVERT_RESULTADO_KEY] = resultado
            st.session_state.last_summary = resultado.get("conversion", [])
            st.session_state.flash_message = ""
        finally:
            st.session_state[CONVERT_EN_CURSO_KEY] = False
        st.rerun()

    resultado = st.session_state.get(CONVERT_RESULTADO_KEY)
    if resultado:
        _render_resultado(resultado)
