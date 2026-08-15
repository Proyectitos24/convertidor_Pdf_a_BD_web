"""
Pestaña "Convertir": subida de PDFs y generación de .db. Comportamiento
idéntico al original, con dos adiciones: tras generar cada .db se lee
su grupo operativo desde la tabla Metadata (fuente de verdad) y se
guarda también en Supabase, en converted_files.grupo; y si la subida a
R2 termina bien pero el INSERT en Supabase falla, se comprueba primero
si la fila llegó a existir de verdad (existe_converted_file) antes de
decidir si borrar ese objeto de R2 — ver
services/upload_compensation.py para el porqué: un INSERT que lanza una
excepción no siempre significa que la fila no se creara (podría haberse
confirmado y haberse perdido solo la respuesta).
"""

from datetime import datetime, timezone

import streamlit as st

from services.conversion_service import convert_uploaded_files
from services.db_metadata_reader import leer_grupo_de_db_bytes
from services.r2_service import build_object_key, upload_db_bytes
from services.store_db import existe_converted_file, insert_converted_file
from services.time_utils import next_midnight_europe_madrid
from services.upload_compensation import ejecutar_con_compensacion_r2


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
            expires_at = next_midnight_europe_madrid()

            saved_count = 0

            for item in generated_files:
                object_key = build_object_key(store["code"], item["name"])
                grupo = leer_grupo_de_db_bytes(item["data"])

                upload_db_bytes(object_key, item["data"])

                ejecutar_con_compensacion_r2(
                    object_key,
                    lambda item=item, object_key=object_key, grupo=grupo: insert_converted_file(
                        store_id=store["id"],
                        original_pdf_name=item["source_pdf"],
                        db_file_name=item["name"],
                        object_key=object_key,
                        size_bytes=len(item["data"]),
                        created_at=created_at,
                        expires_at=expires_at,
                        grupo=grupo,
                    ),
                    lambda object_key=object_key: existe_converted_file(store["id"], object_key),
                )

                saved_count += 1

            st.session_state.flash_message = (
                f"Se guardaron {saved_count} archivos para esta tienda. "
                f"Los archivos, estarán visibles hasta las 00:00"
            )
            st.session_state.last_summary = resumen
            st.rerun()
