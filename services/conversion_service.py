from pathlib import Path
import tempfile

import fitz

import batch_convert
import cajas_azules


def detectar_tipo_pdf(pdf_path: Path) -> str:
    doc = fitz.open(str(pdf_path))
    try:
        first_text = doc[0].get_text("text") or ""
    finally:
        doc.close()

    if cajas_azules.is_rf625a(first_text):
        return "rf625a"

    return "packing"


def convert_uploaded_files(uploaded_files):
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
                        resumen.append(
                            {
                                "archivo": safe_name,
                                "estado": "omitido",
                                "detalle": result["reason"],
                            }
                        )
                        continue

                    db_path = Path(result["db_saved"])
                    generated_files.append(
                        {
                            "name": db_path.name,
                            "data": db_path.read_bytes(),
                            "source_pdf": safe_name,
                        }
                    )

                    resumen.append(
                        {
                            "archivo": safe_name,
                            "estado": "ok",
                            "detalle": db_path.name,
                        }
                    )

                else:
                    tienda, fecha, dest_pdf, generados = batch_convert.process_pdf(pdf_path, out_root)

                    nombres_generados = []

                    for db in generados:
                        db_path = Path(db)
                        generated_files.append(
                            {
                                "name": db_path.name,
                                "data": db_path.read_bytes(),
                                "source_pdf": safe_name,
                            }
                        )
                        nombres_generados.append(db_path.name)

                    resumen.append(
                        {
                            "archivo": safe_name,
                            "estado": "ok",
                            "detalle": ", ".join(nombres_generados),
                        }
                    )

            except Exception as e:
                resumen.append(
                    {
                        "archivo": safe_name,
                        "estado": "error",
                        "detalle": str(e),
                    }
                )

    return generated_files, resumen