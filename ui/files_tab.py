"""
Pestaña "Archivos 24h": listado de los .db generados por el convertidor,
navegado por grupo operativo (Metadata → Supabase.grupo).

Comportamiento definitivo: al entrar se muestran siempre el buscador y
el filtro por grupo (con "Todos los grupos" como opción inicial),
seguidos de un encabezado por cada grupo que tenga archivos visibles
(según filtro/búsqueda), todos cerrados al principio. Es un acordeón
CONTROLADO por session_state (files_tab_grupo_abierto): nunca hay más
de un grupo abierto a la vez. Pulsar un encabezado abre ese grupo y
cierra cualquier otro; pulsar el encabezado ya abierto lo cierra.
Elegir un grupo concreto en el filtro lo autoabre; volver a "Todos los
grupos" cierra todo. Si el grupo abierto se queda sin archivos (por la
búsqueda o por borrar su última fila), se cierra solo.

Rendimiento: el clic en un encabezado usa un callback `on_click` que
actualiza session_state ANTES del único rerun normal que Streamlit ya
dispara tras cualquier interacción de un widget — nunca se llama a
st.rerun() explícitamente para abrir/cerrar un grupo (evitaría un doble
rerun). Los bytes de cada .db solo se piden a R2 para las cards del
grupo actualmente abierto (los grupos cerrados no se recorren en
absoluto, ver el bucle de render_files_tab), y se cachean por
object_key con _descargar_db_bytes_cacheado (st.cache_data, TTL
~10 min) para no repetir la descarga al cerrar y reabrir el mismo
grupo. Un fallo de R2 en una card no rompe las demás: se captura y se
muestra st.error solo en esa fila.

Sin selección múltiple, sin acciones por lote, sin URLs prefirmadas: la
descarga individual usa el patrón original (download_db_bytes +
st.download_button con on_click=mark_file_downloaded), y la eliminación
individual abre un diálogo de confirmación para una sola fila.
"""

import streamlit as st

from services.file_deletion import eliminar_archivos_seguro, purgar_expirados_seguro
from services.file_selection import (
    FILTRO_TODOS,
    agrupar_por_grupo,
    filtrar_archivos,
    grupo_abierto_tras_cambios,
)
from services.grupo_packinglist import GRUPO_ETIQUETAS, GRUPO_ORDEN
from services.r2_service import download_db_bytes
from services.store_db import (
    get_converted_files_for_deletion,
    get_expired_files_pending_purge,
    list_ready_files,
    mark_expired_files,
    mark_file_deleted,
    mark_file_downloaded,
    mark_file_physically_deleted,
)
from ui.formatting import format_dt

FILTRO_KEY = "files_tab_filtro_grupo"
FILTRO_ANTERIOR_KEY = "files_tab_filtro_anterior"
GRUPO_ABIERTO_KEY = "files_tab_grupo_abierto"
BUSQUEDA_KEY = "files_tab_busqueda"

ETIQUETAS_FILTRO = {FILTRO_TODOS: "Todos los grupos", **GRUPO_ETIQUETAS}
OPCIONES_FILTRO = [FILTRO_TODOS] + GRUPO_ORDEN

# ~10 minutos: suficiente para navegar entre grupos sin repetir la
# descarga desde R2, sin dejar bytes de .db cacheados indefinidamente.
CACHE_DB_BYTES_TTL_SEGUNDOS = 600
# Tope defensivo de memoria: no cachear un número ilimitado de .db
# distintos en la misma sesión de Streamlit.
CACHE_DB_BYTES_MAX_ENTRADAS = 128


@st.cache_data(ttl=CACHE_DB_BYTES_TTL_SEGUNDOS, max_entries=CACHE_DB_BYTES_MAX_ENTRADAS, show_spinner=False)
def _descargar_db_bytes_cacheado(object_key):
    """
    Envoltorio cacheado de download_db_bytes, con clave = object_key
    exclusivamente. st.cache_data no memoriza una llamada que lanza una
    excepción, así que un fallo de R2 nunca queda "cacheado como error":
    el siguiente intento vuelve a pedirlo de verdad.
    """
    return download_db_bytes(object_key)


def _alternar_grupo_abierto(grupo):
    """
    Callback on_click del encabezado: se ejecuta antes del rerun normal
    que Streamlit ya dispara tras el clic del botón, así que nunca hace
    falta un st.rerun() adicional aquí.
    """
    abierto_actual = st.session_state.get(GRUPO_ABIERTO_KEY)
    st.session_state[GRUPO_ABIERTO_KEY] = None if abierto_actual == grupo else grupo


@st.dialog("Confirmar eliminación")
def _dialog_confirmar_eliminacion(store_id, archivo):
    st.write(f"Vas a eliminar `{archivo['db_file_name']}`.")

    col1, col2 = st.columns(2)
    confirmar = col1.button("Confirmar eliminación", type="primary", use_container_width=True)
    cancelar = col2.button("Cancelar", use_container_width=True)

    if confirmar:
        eliminados, fallidos, _no_encontrados = eliminar_archivos_seguro(
            store_id,
            [archivo["id"]],
            obtener_filas=get_converted_files_for_deletion,
            marcar_eliminada=mark_file_deleted,
        )
        if fallidos:
            st.session_state["files_tab_flash_error"] = (
                f"No se pudo eliminar '{archivo['db_file_name']}' del "
                f"almacenamiento. Se ha dejado tal cual para reintentar más tarde."
            )
        elif eliminados:
            # Evita servir bytes obsoletos si llegara a reutilizarse el
            # mismo object_key (no debería, pero es una limpieza barata
            # y segura: .clear(object_key) solo borra esa entrada).
            _descargar_db_bytes_cacheado.clear(archivo["object_key"])
            st.session_state["files_tab_flash_success"] = f"Se eliminó '{archivo['db_file_name']}'."
        st.rerun()

    if cancelar:
        st.rerun()


def render_files_tab():
    store = st.session_state.selected_store
    store_id = store["id"]

    st.subheader("Archivos disponibles (hasta las 00:00)")

    flash_success = st.session_state.pop("files_tab_flash_success", None)
    if flash_success:
        st.success(flash_success)
    flash_error = st.session_state.pop("files_tab_flash_error", None)
    if flash_error:
        st.warning(flash_error)

    with st.spinner("Actualizando estado de archivos..."):
        mark_expired_files(store_id)
        purgar_expirados_seguro(
            store_id,
            obtener_pendientes=get_expired_files_pending_purge,
            marcar_purgado=mark_file_physically_deleted,
        )
        archivos = list_ready_files(store_id)

    if not archivos:
        st.info("No hay archivos disponibles para esta tienda.")
        return

    col_busqueda, col_filtro = st.columns([3, 2])
    with col_busqueda:
        texto_busqueda = st.text_input(
            "Buscar por nombre del .db o PDF de origen",
            key=BUSQUEDA_KEY,
            placeholder="p.ej. 20021563094 o RF626A",
        )
    with col_filtro:
        grupo_filtro = st.selectbox(
            "Filtrar por grupo",
            options=OPCIONES_FILTRO,
            format_func=lambda g: ETIQUETAS_FILTRO[g],
            key=FILTRO_KEY,
        )

    visibles = filtrar_archivos(archivos, texto_busqueda, grupo_filtro)
    st.caption(f"Mostrando {len(visibles)} de {len(archivos)} archivos")

    secciones = agrupar_por_grupo(visibles)

    grupo_filtro_anterior = st.session_state.get(FILTRO_ANTERIOR_KEY)
    grupo_abierto_actual = st.session_state.get(GRUPO_ABIERTO_KEY)
    grupo_abierto = grupo_abierto_tras_cambios(
        grupo_abierto_actual, grupo_filtro, grupo_filtro_anterior, secciones
    )
    st.session_state[GRUPO_ABIERTO_KEY] = grupo_abierto
    st.session_state[FILTRO_ANTERIOR_KEY] = grupo_filtro

    if not secciones:
        st.info("Ningún archivo coincide con la búsqueda/filtro actual.")
        return

    for grupo, etiqueta, archivos_grupo in secciones:
        abierto = grupo == grupo_abierto
        flecha = "▼" if abierto else "▶"
        st.button(
            f"{flecha} {etiqueta} ({len(archivos_grupo)})",
            key=f"files_tab_header_{grupo}",
            use_container_width=True,
            on_click=_alternar_grupo_abierto,
            args=(grupo,),
        )

        if abierto:
            for archivo in archivos_grupo:
                _render_fila_archivo(store_id, archivo)


def _render_fila_archivo(store_id, archivo):
    with st.container(border=True):
        col_info, col_descargar, col_borrar = st.columns([4.5, 1.3, 0.6])

        with col_info:
            downloaded = archivo.get("downloaded_at") is not None
            estado = "✅ Descargado" if downloaded else "🕓 Pendiente"
            st.markdown(f"**{archivo['db_file_name']}**")
            st.caption(
                f"{estado} · PDF: {archivo['original_pdf_name']} · "
                f"{archivo['size_bytes']} bytes"
            )
            st.caption(
                f"Creado: {format_dt(archivo['created_at'])} · "
                f"Expira: {format_dt(archivo['expires_at'])}"
            )

        with col_descargar:
            try:
                db_bytes = _descargar_db_bytes_cacheado(archivo["object_key"])
            except Exception:
                st.error("No se pudo descargar. Reintenta.")
            else:
                st.download_button(
                    label="Descargar",
                    data=db_bytes,
                    file_name=archivo["db_file_name"],
                    mime="application/octet-stream",
                    key=f"download_{archivo['id']}",
                    on_click=mark_file_downloaded,
                    args=(archivo["id"],),
                    use_container_width=True,
                )

        with col_borrar:
            if st.button("🗑️", key=f"del_{archivo['id']}", use_container_width=True):
                _dialog_confirmar_eliminacion(store_id, archivo)
