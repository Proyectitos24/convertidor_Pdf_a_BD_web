"""
Detección del formato de un PDF del convertidor por el identificador
explícito de su cabecera interna (CPD-AM613A, CPD-RF626A, CPD-RF625A).

Nunca se usa el nombre del archivo ni el cuerpo del documento: solo
palabras que empiezan por "CPD-" dentro de la franja de cabecera de cada
página (la parte superior, donde el listado imprime "CPD-XXXXXX" en su
segunda línea). Así una descripción de producto no puede cambiar el
formato, y un PDF renombrado se reconoce igual.

Si no aparece ningún identificador, o aparecen identificadores distintos
en el mismo documento, se lanza FormatoPdfError: el llamador debe
informar y NO enviar el PDF a ningún parser.
"""

import re

import fitz

AM613A = "AM613A"
RF626A = "RF626A"
RF625A = "RF625A"

FORMATOS_SOPORTADOS = (AM613A, RF626A, RF625A)

# Fracción superior de la página considerada cabecera. En los listados
# reales el identificador está en la segunda línea (y ≈ 25 pt de 792).
FRANJA_CABECERA = 0.10

_IDENTIFICADOR_RE = re.compile(r"^CPD-([A-Z]{2}\d{3}[A-Z])$")


class FormatoPdfError(Exception):
    """Formato no reconocido o identificadores contradictorios."""


def identificadores_de_cabecera(doc) -> list:
    """Identificadores CPD-* (sin el prefijo) de la cabecera de cada página, en orden."""
    encontrados = []
    for page in doc:
        limite = page.rect.height * FRANJA_CABECERA
        for x0, y0, x1, y1, palabra, *_ in page.get_text("words"):
            if y0 > limite:
                continue
            m = _IDENTIFICADOR_RE.match(palabra)
            if m:
                encontrados.append(m.group(1))
    return encontrados


def detectar_formato_doc(doc) -> str:
    ids = identificadores_de_cabecera(doc)
    distintos = sorted(set(ids))

    if not distintos:
        raise FormatoPdfError(
            "Formato no reconocido: la cabecera no contiene CPD-AM613A, CPD-RF626A ni CPD-RF625A"
        )
    if len(distintos) > 1:
        raise FormatoPdfError(
            "Identificadores de formato contradictorios en la cabecera: "
            + ", ".join(f"CPD-{i}" for i in distintos)
        )

    formato = distintos[0]
    if formato not in FORMATOS_SOPORTADOS:
        raise FormatoPdfError(f"Formato CPD-{formato} no soportado")
    return formato


def detectar_formato_pdf(pdf_path) -> str:
    doc = fitz.open(str(pdf_path))
    try:
        return detectar_formato_doc(doc)
    finally:
        doc.close()


def detectar_formato_bytes(data: bytes) -> str:
    try:
        doc = fitz.open(stream=bytes(data), filetype="pdf")
    except Exception as e:
        raise FormatoPdfError(f"No se pudo abrir el PDF: {e}") from e
    try:
        return detectar_formato_doc(doc)
    finally:
        doc.close()
