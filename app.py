from pathlib import Path
import tempfile

import fitz
import streamlit as st

import batch_convert
import cajas_azules


st.set_page_config(page_title="Convertidor PDF → DB", layout="centered")
st.title("Convertidor PDF → DB")
st.write("Sube uno o varios PDF y descarga los archivos .db")


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

if st.button("Convertir"):
    if not uploaded_files:
        st.warning("Primero sube al menos un PDF.")
    else:
        resumen = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            input_dir = tmp_root / "entrada"
            out_root = tmp_root / "salida"

            input_dir.mkdir(parents=True, exist_ok=True)
            out_root.mkdir(parents=True, exist_ok=True)

            generated_db_files = []

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
                        generated_db_files.append(db_path)

                        resumen.append({
                            "archivo": safe_name,
                            "estado": "ok",
                            "detalle": db_path.name,
                        })

                    else:
                        tienda, fecha, dest_pdf, generados = batch_convert.process_pdf(
                            pdf_path, out_root
                        )

                        for db in generados:
                            generated_db_files.append(Path(db))

                        resumen.append({
                            "archivo": safe_name,
                            "estado": "ok",
                            "detalle": ", ".join(Path(x).name for x in generados),
                        })

                except Exception as e:
                    resumen.append({
                        "archivo": safe_name,
                        "estado": "error",
                        "detalle": str(e),
                    })

            if not generated_db_files:
                st.error("No se generó ningún .db")
                st.write(resumen)
            else:
                st.success(f"Conversión completada. DB generados: {len(generated_db_files)}")
                st.write(resumen)

                st.subheader("Descargar archivos .db")

                for i, db_path in enumerate(generated_db_files, start=1):
                    db_bytes = db_path.read_bytes()

                    st.download_button(
                        label=f"Descargar {db_path.name}",
                        data=db_bytes,
                        file_name=db_path.name,
                        mime="application/octet-stream",
                        key=f"db_{i}_{db_path.name}",
                    )