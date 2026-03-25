from pathlib import Path
import tempfile

import fitz
import streamlit as st

import batch_convert
import cajas_azules


st.set_page_config(page_title="Convertidor PDF → DB", layout="centered")
st.title("Convertidor PDF → DB")
st.write("Sube uno o varios PDF y descarga los archivos .db")


if "generated_files" not in st.session_state:
    st.session_state.generated_files = []

if "conversion_resumen" not in st.session_state:
    st.session_state.conversion_resumen = []


def detectar_tipo_pdf(pdf_path: Path) -> str:
    doc = fitz.open(str(pdf_path))
    try:
        first_text = doc[0].get_text("text") or ""
    finally:
        doc.close()

    if cajas_azules.is_rf625a(first_text):
        return "rf625a"

    return "packing"


uploaded_files = st.file_uploader(
    "Selecciona uno o varios PDF",
    type=["pdf"],
    accept_multiple_files=True,
)

col1, col2 = st.columns([1, 1])

with col1:
    convertir = st.button("Convertir", use_container_width=True)

with col2:
    limpiar = st.button("Limpiar resultados", use_container_width=True)

if limpiar:
    st.session_state.generated_files = []
    st.session_state.conversion_resumen = []
    st.rerun()

if convertir:
    if not uploaded_files:
        st.warning("Primero sube al menos un PDF.")
    else:
        resumen = []
        generated_files = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            input_dir = tmp_root / "entrada"
            out_root = tmp_root / "salida"

            input_dir.mkdir(parents=True, exist_ok=True)
            out_root.mkdir(parents=True, exist_ok=True)

            for uploaded_file in uploaded_files:
                safe_name = Path(uploaded_file.name).name
                pdf_path = input_dir / safe_name
                pdf_path.write_bytes(uploaded_file.getvalue())

                try:
                    tipo = detectar_tipo_pdf(pdf_path)

                    if tipo == "rf625a":
                        result = cajas_azules.process_pdf(pdf_path, out_root)

                        if not result["ok"]:
                            resumen.append({
                                "archivo": safe_name,
                                "estado": "omitido",
                                "detalle": result["reason"],
                            })
                            continue

                        db_path = Path(result["db_saved"])
                        generated_files.append({
                            "name": db_path.name,
                            "data": db_path.read_bytes(),
                        })

                        resumen.append({
                            "archivo": safe_name,
                            "estado": "ok",
                            "detalle": db_path.name,
                        })

                    else:
                        tienda, fecha, dest_pdf, generados = batch_convert.process_pdf(
                            pdf_path, out_root
                        )

                        nombres_generados = []

                        for db in generados:
                            db_path = Path(db)
                            generated_files.append({
                                "name": db_path.name,
                                "data": db_path.read_bytes(),
                            })
                            nombres_generados.append(db_path.name)

                        resumen.append({
                            "archivo": safe_name,
                            "estado": "ok",
                            "detalle": ", ".join(nombres_generados),
                        })

                except Exception as e:
                    resumen.append({
                        "archivo": safe_name,
                        "estado": "error",
                        "detalle": str(e),
                    })

        st.session_state.generated_files = generated_files
        st.session_state.conversion_resumen = resumen

if st.session_state.generated_files:
    st.success(
        f"Conversión completada. DB generados: {len(st.session_state.generated_files)}"
    )
    st.write(st.session_state.conversion_resumen)

    st.subheader("Descargar archivos .db")

    for i, item in enumerate(st.session_state.generated_files, start=1):
        st.download_button(
            label=f"Descargar {item['name']}",
            data=item["data"],
            file_name=item["name"],
            mime="application/octet-stream",
            key=f"db_{i}_{item['name']}",
            on_click="ignore",
            use_container_width=True,
        )
elif st.session_state.conversion_resumen:
    st.error("No se generó ningún .db")
    st.write(st.session_state.conversion_resumen)