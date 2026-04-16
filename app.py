from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

import streamlit as st

from services.conversion_service import convert_uploaded_files
from services.r2_service import build_object_key, upload_db_bytes, download_db_bytes
from services.store_db import (
    get_store_by_code,
    insert_converted_file,
    list_active_stores,
    list_ready_files,
    mark_expired_files,
    mark_file_downloaded,
)


st.set_page_config(page_title="Convertidor PDF → DB", layout="centered")
st.markdown("""
<style>
div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlockBorderWrapper"] {
    padding: 0.45rem 0.8rem 0.45rem 0.8rem;
}
div[data-testid="stDownloadButton"] > button {
    min-height: 42px;
}
</style>
""", unsafe_allow_html=True)




APP_TZ = ZoneInfo("Europe/Madrid")


def next_midnight_utc():
    now_local = datetime.now(APP_TZ)
    tomorrow = now_local.date() + timedelta(days=1)
    midnight_local = datetime.combine(tomorrow, time.min, tzinfo=APP_TZ)
    return midnight_local.astimezone(timezone.utc)


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
            expires_at = next_midnight_utc()

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
                f"Los archivos, estarán visibles hasta las 00:00"
            )
            st.session_state.last_summary = resumen
            st.rerun()


def render_files_tab():
    store = st.session_state.selected_store

    st.subheader("Archivos disponibles (hasta las 00:00)")

    mark_expired_files(store["id"])
    files = list_ready_files(store["id"])

    if not files:
        st.info("No hay archivos disponibles para esta tienda.")
        return

    for row in files:
        downloaded = row.get("downloaded_at") is not None
        db_bytes = download_db_bytes(row["object_key"])

        with st.container(border=True):
            col_info, col_btn = st.columns([4.7, 1.3])

            with col_info:
                estado = "✅ Descargado" if downloaded else "🕓 Pendiente"

                st.markdown(f"**{row['db_file_name']}**")

                info_html = f"""
                    <div style="line-height:1.25; font-size:0.95rem; margin-top:0.2rem; padding-bottom:0.35rem;">
                        <div style="margin-bottom:0.28rem;">
                            {estado} | PDF: {row['original_pdf_name']} | Tamaño: {row['size_bytes']} bytes
                        </div>
                        <div style="margin-bottom:0.28rem;">
                            Creado: {format_dt(row['created_at'])} | Expira: {format_dt(row['expires_at'])}
                        </div>
                        {"<div style='margin-bottom:0.25rem;'>Descargado: " + format_dt(row['downloaded_at']) + "</div>" if downloaded else ""}
                    </div>
                """
                st.markdown(info_html, unsafe_allow_html=True)

            with col_btn:
                st.download_button(
                    label="Volver a descargar" if downloaded else "Descargar",
                    data=db_bytes,
                    file_name=row["db_file_name"],
                    mime="application/octet-stream",
                    key=f"download_{row['id']}",
                    on_click=mark_file_downloaded,
                    args=(row["id"],),
                    use_container_width=True,
                )


def main():
    init_state()

    if not st.session_state.is_logged_in or not st.session_state.selected_store:
        show_login()
        return

    st.title("Convertidor PDF → DB")
    render_header()

    if st.session_state.get("flash_message"):
        st.success(st.session_state["flash_message"])

    tab1, tab2 = st.tabs(["Convertir", "Archivos 24h"])

    with tab1:
        render_convert_tab()

    with tab2:
        render_files_tab()


if __name__ == "__main__":
    main()