import streamlit as st

from ui.auth import init_state, render_header, show_login
from ui.convert_tab import render_convert_tab
from ui.files_tab import render_files_tab
from ui.shared_tab import render_shared_files_tab


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


def main():
    init_state()

    if not st.session_state.is_logged_in or not st.session_state.selected_store:
        show_login()
        return

    st.title("Convertidor PDF → DB")
    render_header()

    if st.session_state.get("flash_message"):
        st.success(st.session_state["flash_message"])

    tab1, tab2, tab3 = st.tabs(["Convertir", "Archivos 24h", "Compartir APK/PDF"])

    with tab1:
        render_convert_tab()

    with tab2:
        render_files_tab()

    with tab3:
        render_shared_files_tab()


if __name__ == "__main__":
    main()
