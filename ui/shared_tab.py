"""
Pestaña "Compartir APK/PDF": cada tienda sube y ve exclusivamente sus
propios archivos (no hay administrador, no hay tienda destinataria
distinta — ver decisiones aprobadas). Caduca igual que "Archivos 24h":
a las 00:00 Europe/Madrid del día siguiente a la subida.

Descarga directa de un solo paso: cada card genera automáticamente una
URL prefirmada al renderizarse (nunca se cargan los bytes del
APK/PDF —hasta 150 MB, el límite real de MAX_SHARED_FILE_BYTES— en
memoria del servidor) y la muestra como un único enlace con apariencia
de botón. No existe ningún paso manual previo para obtener ese enlace:
si la URL guardada en session_state ya caducó (o no existe todavía),
se regenera sola antes de pintarlo. La URL se guarda con su instante
de caducidad (UTC, con margen de seguridad) usando
services.presigned_url_cache.

Como un enlace directo no permite confirmar que el navegador terminó
la descarga, esta pestaña ya no registra ningún instante de descarga
ni muestra ningún estado al respecto: solo "Disponible · X bytes". La
columna correspondiente sigue existiendo en la tabla (no se migra
nada), simplemente esta interfaz dejó de escribirla/leerla.

El enlace se pinta como HTML (`st.markdown(..., unsafe_allow_html=True)`
con un `<a href download>`) en vez del widget nativo de enlace de
Streamlit, que abre una pestaña nueva; un `<a>` sin target se descarga
en el contexto actual gracias al Content-Disposition: attachment que ya
fuerza r2_service.generate_download_url. La URL y el nombre de archivo
siempre se escapan con html.escape antes de interpolarse.
"""

import html
from datetime import datetime, timezone

import streamlit as st

from services.file_deletion import eliminar_archivos_seguro, purgar_expirados_seguro
from services.presigned_url_cache import URL_EXPIRES_IN_SEGUNDOS, construir_entrada, entrada_valida
from services.r2_service import generate_download_url, upload_fileobj
from services.shared_files_db import (
    existe_shared_file,
    get_expired_shared_files_pending_purge,
    get_shared_files_for_deletion,
    insert_shared_file,
    list_ready_shared_files,
    mark_expired_shared_files,
    mark_shared_file_deleted,
    mark_shared_file_physically_deleted,
)
from services.shared_files_service import (
    ArchivoInvalido,
    build_shared_object_key,
    validar_archivo_compartido,
)
from services.time_utils import next_midnight_europe_madrid
from services.upload_compensation import ejecutar_con_compensacion_r2
from ui.formatting import format_dt

_ENLACE_DESCARGA_ESTILO = (
    "display:inline-block;width:100%;box-sizing:border-box;text-align:center;"
    "padding:0.5rem 1rem;border-radius:0.5rem;border:1px solid rgba(49,51,63,0.4);"
    "background-color:transparent;color:inherit;text-decoration:none;font-weight:600;"
)


@st.dialog("Confirmar eliminación")
def _dialog_confirmar_eliminacion_compartido(store_id, archivo):
    st.write(f"Vas a eliminar `{archivo['original_file_name']}`.")

    col1, col2 = st.columns(2)
    confirmar = col1.button("Confirmar eliminación", type="primary", use_container_width=True)
    cancelar = col2.button("Cancelar", use_container_width=True)

    if confirmar:
        eliminados, fallidos, _no_encontrados = eliminar_archivos_seguro(
            store_id,
            [archivo["id"]],
            obtener_filas=get_shared_files_for_deletion,
            marcar_eliminada=mark_shared_file_deleted,
        )
        if fallidos:
            st.session_state["shared_tab_flash_error"] = (
                f"No se pudo eliminar '{archivo['original_file_name']}' del "
                f"almacenamiento. Se ha dejado tal cual para reintentar más tarde."
            )
        elif eliminados:
            st.session_state["shared_tab_flash_success"] = f"Se eliminó '{archivo['original_file_name']}'."
        st.rerun()

    if cancelar:
        st.rerun()


def _uploader_key():
    contador = st.session_state.get("shared_tab_uploader_contador", 0)
    return f"shared_tab_uploader_{contador}"


def _render_subida(store):
    uploaded_file = st.file_uploader(
        "Selecciona un archivo .apk o .pdf",
        type=["apk", "pdf"],
        accept_multiple_files=False,
        key=_uploader_key(),
    )

    if st.button("Subir y compartir", type="primary", use_container_width=True, key="shared_tab_btn_subir"):
        if uploaded_file is None:
            st.warning("Primero selecciona un archivo .apk o .pdf.")
            return

        try:
            metadatos = validar_archivo_compartido(
                uploaded_file.name, uploaded_file.size, uploaded_file
            )
        except ArchivoInvalido as exc:
            st.error(str(exc))
            return

        with st.spinner("Subiendo archivo..."):
            object_key = build_shared_object_key(
                store["code"], metadatos["file_kind"], metadatos["original_file_name"]
            )

            # Objeto file-like ya validado y reposicionado en 0: se sube
            # tal cual, sin materializar una copia extra en memoria.
            uploaded_file.seek(0)
            upload_fileobj(object_key, uploaded_file, metadatos["content_type"])

            created_at = datetime.now(timezone.utc)
            expires_at = next_midnight_europe_madrid()

            ejecutar_con_compensacion_r2(
                object_key,
                lambda: insert_shared_file(
                    store_id=store["id"],
                    file_kind=metadatos["file_kind"],
                    original_file_name=metadatos["original_file_name"],
                    object_key=object_key,
                    content_type=metadatos["content_type"],
                    size_bytes=uploaded_file.size,
                    created_at=created_at,
                    expires_at=expires_at,
                ),
                lambda: existe_shared_file(store["id"], object_key),
            )

        st.session_state["shared_tab_uploader_contador"] = (
            st.session_state.get("shared_tab_uploader_contador", 0) + 1
        )
        st.session_state["shared_tab_flash_success"] = (
            f"Se subió '{metadatos['original_file_name']}'. Estará disponible "
            f"hasta las {format_dt(expires_at.isoformat())}."
        )
        st.rerun()


def _url_descarga_directa(archivo):
    """
    URL prefirmada lista para usar, generándola (o regenerándola) sola
    si hace falta. Se guarda en session_state con su caducidad (vía
    services.presigned_url_cache) para no volver a firmar en cada
    rerun mientras siga siendo válida.
    """
    clave_url = f"shared_tab_url_{archivo['id']}"
    entrada = st.session_state.get(clave_url)

    if not entrada_valida(entrada):
        url = generate_download_url(
            archivo["object_key"],
            archivo["original_file_name"],
            expires_in=URL_EXPIRES_IN_SEGUNDOS,
        )
        entrada = construir_entrada(url, URL_EXPIRES_IN_SEGUNDOS)
        st.session_state[clave_url] = entrada

    return entrada["url"]


def _render_enlace_descarga(url, nombre_archivo):
    """
    Enlace con apariencia de botón que descarga en la pestaña actual
    (nunca se abre una pestaña nueva: no hay target="_blank"). URL y
    nombre siempre escapados con html.escape antes de interpolarse en
    el HTML.
    """
    url_segura = html.escape(url, quote=True)
    nombre_seguro = html.escape(nombre_archivo, quote=True)
    st.markdown(
        f'<a href="{url_segura}" download="{nombre_seguro}" '
        f'style="{_ENLACE_DESCARGA_ESTILO}">Descargar</a>',
        unsafe_allow_html=True,
    )


def _render_fila_compartido(store_id, archivo):
    with st.container(border=True):
        col_info, col_desc, col_borrar = st.columns([3.2, 1.4, 1.4])

        with col_info:
            st.markdown(f"**{archivo['original_file_name']}**")
            st.caption(f"Disponible · {archivo['size_bytes']} bytes")
            st.caption(
                f"Expira: {format_dt(archivo['expires_at'])} "
                f"(medianoche Europe/Madrid del día siguiente a la subida)"
            )

        with col_desc:
            url = _url_descarga_directa(archivo)
            _render_enlace_descarga(url, archivo["original_file_name"])

        with col_borrar:
            if st.button("Eliminar", key=f"shared_tab_del_{archivo['id']}", use_container_width=True):
                _dialog_confirmar_eliminacion_compartido(store_id, archivo)


def render_shared_files_tab():
    store = st.session_state.selected_store
    store_id = store["id"]

    st.subheader("Compartir APK/PDF")
    st.caption(
        "Sube un archivo .apk o .pdf para esta tienda. Estará disponible "
        "hasta las 00:00 (hora de Madrid) del día siguiente a la subida, "
        "visible solo para esta tienda."
    )

    flash_success = st.session_state.pop("shared_tab_flash_success", None)
    if flash_success:
        st.success(flash_success)
    flash_error = st.session_state.pop("shared_tab_flash_error", None)
    if flash_error:
        st.warning(flash_error)

    _render_subida(store)

    st.divider()

    with st.spinner("Actualizando estado de archivos..."):
        mark_expired_shared_files(store_id)
        purgar_expirados_seguro(
            store_id,
            obtener_pendientes=get_expired_shared_files_pending_purge,
            marcar_purgado=mark_shared_file_physically_deleted,
        )
        archivos = list_ready_shared_files(store_id)

    if not archivos:
        st.info("No hay archivos compartidos activos para esta tienda.")
        return

    apks = [a for a in archivos if a["file_kind"] == "apk"]
    pdfs = [a for a in archivos if a["file_kind"] == "pdf"]

    st.markdown("### Aplicaciones APK")
    if apks:
        for archivo in apks:
            _render_fila_compartido(store_id, archivo)
    else:
        st.caption("Sin APK activos.")

    st.markdown("### Documentos PDF")
    if pdfs:
        for archivo in pdfs:
            _render_fila_compartido(store_id, archivo)
    else:
        st.caption("Sin PDF activos.")
