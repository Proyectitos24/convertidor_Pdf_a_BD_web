"""
Conversión de los PDF subidos en una operación a .db en memoria.

Evita trabajo y resultados duplicados DENTRO de una misma operación:

  - PDFs idénticos (mismo SHA-256 del contenido, aunque tengan nombres
    distintos) se procesan una sola vez; los repetidos quedan en el
    resumen con estado "duplicado".
  - Cada PDF se procesa en su propio subdirectorio temporal de entrada
    y de salida, así que dos PDFs distintos con el mismo nombre nunca
    se pisan entre sí (ni el PDF ni los .db que generan).
  - Si varios PDFs generan un .db con el mismo nombre, se compara su
    huella lógica (services/db_fingerprint.py) cuando ya se conocen
    TODAS las apariciones del lote:
      * todas con la misma huella -> se conserva solo la primera; las
        demás quedan como "duplicado";
      * alguna huella distinta -> el nombre es conflictivo durante toda
        la operación: NO se entrega ninguna versión (ni la primera, ni
        una posterior aunque coincida con otra) y el resumen incluye
        una entrada "conflicto" con todos los PDFs implicados.

Los parsers, nombres de .db y contenido de cada .db no cambian: los
bytes entregados son exactamente los que escribe process_pdf.

Elección del parser: SOLO por el identificador interno de la cabecera
(services/pdf_format.py: CPD-AM613A, CPD-RF626A, CPD-RF625A), nunca por
el nombre del archivo. Un PDF sin identificador reconocible o con
identificadores contradictorios queda como "error" y no se envía a
ningún parser.

AM613A (services/am613a_converter.py) necesita además el grupo elegido
manualmente para ese PDF, identificado por el SHA-256 de su contenido
(grupos_am613a = {sha256: grupo}); sin grupo válido no se convierte.
"""

from pathlib import Path
import hashlib
import tempfile

import fitz

import batch_convert
import cajas_azules
from services import am613a_converter
from services.db_fingerprint import huella_logica_de_bytes
from services.grupo_packinglist import GRUPOS_VALIDOS
from services.pdf_format import AM613A, RF625A, RF626A, detectar_formato_bytes, detectar_formato_pdf


def detectar_formato(pdf_path: Path) -> str:
    """Formato por identificador interno de cabecera; lanza FormatoPdfError si no es fiable."""
    return detectar_formato_pdf(pdf_path)


def detectar_tipo_pdf(pdf_path: Path) -> str:
    """Compatibilidad: 'rf625a' para RF625A, 'packing' para RF626A (lanza en otro caso)."""
    formato = detectar_formato(pdf_path)
    if formato == RF625A:
        return "rf625a"
    if formato == RF626A:
        return "packing"
    raise ValueError(f"detectar_tipo_pdf no admite el formato {formato}")


def am613a_de_subidas(uploaded_files, cache=None):
    """
    AM613A únicos (por SHA-256 del contenido) entre los PDF subidos, en
    orden de primera aparición: [(sha256, nombre_del_primero), ...].
    cache: dict opcional {sha256: formato | None} para no reabrir los
    mismos PDF en cada rerun. Un PDF ilegible o sin formato reconocido
    no aparece aquí: lo informará la conversión.
    """
    cache = {} if cache is None else cache
    vistos = set()
    resultado = []
    for uploaded_file in uploaded_files or []:
        contenido = uploaded_file.getvalue()
        sha = sha256_bytes(contenido)
        if sha in vistos:
            continue
        vistos.add(sha)
        if sha not in cache:
            try:
                cache[sha] = detectar_formato_bytes(contenido)
            except Exception:
                cache[sha] = None
        if cache[sha] == AM613A:
            resultado.append((sha, Path(uploaded_file.name).name))
    return resultado


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _leer_db_generado(db_path: Path):
    data = db_path.read_bytes()
    return db_path.name, data, huella_logica_de_bytes(data)


def _etiqueta_pdf(archivo, indice):
    return f"{archivo} (n.º {indice + 1})"


def _resolver_nombres(resumen_por_pdf, versiones):
    """
    Decide, con el lote completo, qué .db se entregan y construye el
    resumen final.

    resumen_por_pdf: entradas por PDF en orden de entrada; las de PDFs
    convertidos llevan "_indice" y "_nombres" (pendientes de resolver).
    versiones: nombre -> [(indice_pdf, item), ...] en orden de aparición.
    """
    conflictivos = {
        nombre
        for nombre, apariciones in versiones.items()
        if len({item["fingerprint"] for _indice, item in apariciones}) > 1
    }

    generated_files = [
        apariciones[0][1]
        for nombre, apariciones in versiones.items()
        if nombre not in conflictivos
    ]

    resumen = []
    for entrada in resumen_por_pdf:
        if "_nombres" not in entrada:
            resumen.append(entrada)
            continue

        indice = entrada["_indice"]
        archivo = entrada["archivo"]
        entregados = []
        duplicados = []
        for nombre in entrada["_nombres"]:
            if nombre in conflictivos:
                continue  # se informa abajo, en la entrada de conflicto
            indice_primero, primero = versiones[nombre][0]
            if indice_primero == indice:
                entregados.append(nombre)
            else:
                duplicados.append((nombre, _etiqueta_pdf(primero["source_pdf"], indice_primero)))

        if entregados or not entrada["_nombres"]:
            resumen.append({"archivo": archivo, "estado": "ok", "detalle": ", ".join(entregados)})
        for nombre, origen in duplicados:
            resumen.append(
                {
                    "archivo": archivo,
                    "estado": "duplicado",
                    "detalle": f"{nombre}: mismo contenido que el generado desde {origen}; se omite",
                }
            )

    for nombre, apariciones in versiones.items():
        if nombre not in conflictivos:
            continue
        pdfs = [_etiqueta_pdf(item["source_pdf"], indice) for indice, item in apariciones]
        resumen.append(
            {
                "archivo": ", ".join(item["source_pdf"] for _indice, item in apariciones),
                "estado": "conflicto",
                "db_file_name": nombre,
                "pdfs": pdfs,
                "detalle": (
                    f"{nombre}: generado con datos distintos por {', '.join(pdfs)}; "
                    f"no se guarda ninguna versión de este nombre"
                ),
            }
        )

    return generated_files, resumen


def convert_uploaded_files(uploaded_files, grupos_am613a=None):
    grupos_am613a = grupos_am613a or {}
    resumen_por_pdf = []
    versiones = {}
    vistos_por_sha = {}

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)

        for indice, uploaded_file in enumerate(uploaded_files):
            safe_name = Path(uploaded_file.name).name
            contenido = uploaded_file.getvalue()
            sha = sha256_bytes(contenido)

            if sha in vistos_por_sha:
                original = vistos_por_sha[sha]
                detalle = (
                    "PDF repetido en esta operación"
                    if original == safe_name
                    else f"mismo contenido que {original}"
                )
                resumen_por_pdf.append(
                    {
                        "archivo": safe_name,
                        "estado": "duplicado",
                        "detalle": f"{detalle}; se procesa una sola vez",
                    }
                )
                continue
            vistos_por_sha[sha] = safe_name

            entrada_dir = tmp_root / f"{indice:04d}" / "entrada"
            out_root = tmp_root / f"{indice:04d}" / "salida"
            entrada_dir.mkdir(parents=True, exist_ok=True)
            out_root.mkdir(parents=True, exist_ok=True)

            pdf_path = entrada_dir / safe_name
            pdf_path.write_bytes(contenido)

            try:
                formato = detectar_formato(pdf_path)

                if formato == AM613A:
                    grupo = grupos_am613a.get(sha)
                    if grupo not in GRUPOS_VALIDOS:
                        raise ValueError("AM613A sin grupo elegido; elige un grupo y vuelve a convertir")
                    result = am613a_converter.process_pdf(pdf_path, out_root, grupo)
                    db_paths = [Path(result["db_saved"])]
                elif formato == RF625A:
                    result = cajas_azules.process_pdf(pdf_path, out_root)

                    if not result["ok"]:
                        resumen_por_pdf.append(
                            {
                                "archivo": safe_name,
                                "estado": "omitido",
                                "detalle": result["reason"],
                            }
                        )
                        continue

                    db_paths = [Path(result["db_saved"])]
                else:
                    _tienda, _fecha, _dest_pdf, generados = batch_convert.process_pdf(pdf_path, out_root)
                    db_paths = [Path(db) for db in generados]

                # Se leen y se calculan TODAS las huellas de este PDF antes
                # de registrar ninguna: si una falla, no queda medio PDF
                # registrado.
                leidos = [_leer_db_generado(db_path) for db_path in db_paths]
                for nombre, data, huella in leidos:
                    versiones.setdefault(nombre, []).append(
                        (
                            indice,
                            {"name": nombre, "data": data, "source_pdf": safe_name, "fingerprint": huella},
                        )
                    )
                resumen_por_pdf.append(
                    {
                        "archivo": safe_name,
                        "estado": "ok",
                        "_indice": indice,
                        "_nombres": [nombre for nombre, _data, _huella in leidos],
                    }
                )

            except Exception as e:
                resumen_por_pdf.append(
                    {
                        "archivo": safe_name,
                        "estado": "error",
                        "detalle": str(e),
                    }
                )

    return _resolver_nombres(resumen_por_pdf, versiones)
