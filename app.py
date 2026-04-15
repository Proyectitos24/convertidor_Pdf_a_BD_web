from datetime import datetime, timedelta, timezone

import streamlit as st

from services.conversion_service import convert_uploaded_files
from services.r2_service import build_object_key, generate_download_url, upload_db_bytes
from services.store_db import (
    get_store_by_code,
    insert_converted_file,
    list_active_stores,
    list_ready_files,
    mark_expired_files,
)


st.set_page_config(page_title="Convertidor PDF → DB", layout="centered")


def init_state():
    defaults = {
        "is_logged_in": False,
        "selected_store": None,
        "flash_message": "",
        "last_summary": [],
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def clear_session():
    st.session_state.is_logged_in = False
    st.session_state.selected_store = None
    st.session_state.flash_message = ""
    st.session_state.last_summary = []


def format_dt(value: str) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    dt = dt.astimezone()
    return dt.strftime("%d/%m/%Y %H:%M")


def validate_store_password(store_code: str, password: str) -> bool:
    store_passwords = st.secrets["store_passwords"]
    expected_password = store_passwords.get(store_code)
    return expected_password == password


def show_login():
    st.title("Acceso tiendas")
    st.write("Selecciona tu tienda y escribe la clave.")

    stores = list_active_stores()

    if not stores:
        st.error("No hay tiendas activas configuradas.")
        st.stop()

    store_labels = [f"{store['code']} - {store['name']}" for store in stores]
    store_map = {f"{store['code']} - {store['name']}": store for store in stores}

    with st.form("login_form"):
        selected_label = st.selectbox("Seleccione su tienda", store_labels)
        password = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Entrar", use_container_width=True)

    if submitted:
        if not password:
            st.warning("Escribe la contraseña.")
            return

        selected_store = store_map[selected_label]
        store_code = selected_store["code"]

        if not validate_store_password(store_code, password):
            st.error("Contraseña incorrecta.")
            return

        real_store = get_store_by_code(store_code)

        if not real_store:
            st.error("La tienda no está activa.")
            return

        st.session_state.is_logged_in = True
        st.session_state.selected_store = real_store
        st.rerun()


def render_header():
    store = st.session_state.selected_store

    col1, col2 = st.columns([4, 1])

    with col1:
        st.info(f"Tienda: {store['code']} - {store['name']}")

    with col2:
        if st.button("Salir", use_container_width=True):
            clear_session()
            st.rerun()


def render_convert_tab():
    store = st.session_state.selected_store

    st.subheader("Convertir PDFs")

    uploaded_files = st.file_uploader(
        "Selecciona uno o varios PDF",
        type=["pdf"],
        accept_multiple_files=True,
        key="pdf_uploader",
    )

    if st.button("Convertir y guardar 24h", type="primary", use_container_width=True):
        if not uploaded_files:
            st.warning("Primero sube al menos un PDF.")
            return

        with st.spinner("Convirtiendo y guardando archivos..."):
            generated_files, resumen = convert_uploaded_files(uploaded_files)

            if not generated_files:
                st.error("No se generó ningún .db")
                st.write(resumen)
                return

            created_at = datetime.now(timezone.utc)
            expires_at = created_at + timedelta(hours=24)

            saved_count = 0

            for item in generated_files:
                object_key = build_object_key(store["code"], item["name"])

                upload_db_bytes(object_key, item["data"])

                insert_converted_file(
                    store_id=store["id"],
                    original_pdf_name=item["source_pdf"],
                    db_file_name=item["name"],
                    object_key=object_key,
                    size_bytes=len(item["data"]),
                    created_at=created_at,
                    expires_at=expires_at,
                )

                saved_count += 1

            st.session_state.flash_message = (
                f"Se guardaron {saved_count} archivos para esta tienda. "
                f"Estarán visibles 24 horas."
            )
            st.session_state.last_summary = resumen
            st.rerun()


def render_files_tab():
    store = st.session_state.selected_store

    st.subheader("Archivos disponibles (24h)")

    mark_expired_files(store["id"])
    files = list_ready_files(store["id"])

    if not files:
        st.info("No hay archivos disponibles para esta tienda.")
        return

    for row in files:
        url = generate_download_url(
            object_key=row["object_key"],
            download_name=row["db_file_name"],
            expires_in=900,
        )

        with st.container(border=True):
            st.write(f"**{row['db_file_name']}**")
            st.caption(f"PDF origen: {row['original_pdf_name']}")
            st.caption(
                f"Creado: {format_dt(row['created_at'])} | "
                f"Expira: {format_dt(row['expires_at'])}"
            )
            st.caption(f"Tamaño: {row['size_bytes']} bytes")
            st.link_button("Descargar", url, use_container_width=True)


def main():
    init_state()

    if not st.session_state.is_logged_in or not st.session_state.selected_store:
        show_login()
        return

    st.title("Convertidor PDF → DB")
    render_header()

    if st.session_state.flash_message:
        st.success(st.session_state.flash_message)

    if st.session_state.last_summary:
        st.write(st.session_state.last_summary)

    tab1, tab2 = st.tabs(["Convertir", "Archivos 24h"])

    with tab1:
        render_convert_tab()

    with tab2:
        render_files_tab()


if __name__ == "__main__":
    main()