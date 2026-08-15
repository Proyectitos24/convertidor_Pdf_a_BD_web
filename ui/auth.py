"""
Login por tienda y cabecera de sesión. Extraído verbatim de app.py: el
comportamiento es exactamente el mismo de antes, solo cambia dónde vive
el código, para que app.py no sea un archivo monolítico.
"""

import streamlit as st

from services.store_db import get_store_by_code, list_active_stores


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
