"""
Generador de listados RF626A y RF625A sintéticos para pruebas portables
(no es una prueba en sí). Reproduce la rejilla monoespaciada de los
listados reales (Courier 8 pt = 4,8 pt por carácter, página 950x792):
dos mitades por página, cabecera con la columna "Formato" en la misma
posición que en los PDFs reales y cada línea de producto con su
Cantidad y su Formato.

Posiciones (columna de carácter) medidas en los PDFs reales:
  RF626A izquierda: código termina en 11, descripción en 13, Cantidad
  termina en 54, Formato en 59 (cabecera "Formato" en 56-62), Total
  unidades termina en 72. Mitad derecha: +105 columnas.
  RF625A izquierda: código en 6, descripción en 14, Cantidad termina
  en 68, Formato en 74 (cabecera en 71-77). Mitad derecha: +93.

Todo se escribe en la ruta que indique la prueba (TemporaryDirectory).
"""

import fitz

ALTO_LINEA = 12
MARGEN_SUPERIOR = 20


def _linea(piezas):
    piezas = [(c, t) for c, t in piezas if t]
    if not piezas:
        return ""
    ancho = max(c + len(t) for c, t in piezas)
    buf = [" "] * ancho
    for c, t in piezas:
        buf[c:c + len(t)] = list(t)
    return "".join(buf).rstrip()


def escribir_pdf(ruta, paginas):
    doc = fitz.open()
    for lineas in paginas:
        page = doc.new_page(width=950, height=792)
        for i, linea in enumerate(lineas):
            if linea:
                page.insert_text((0, MARGEN_SUPERIOR + i * ALTO_LINEA), linea, fontname="cour", fontsize=8)
    doc.save(str(ruta))
    doc.close()
    return ruta


# ---------------------------------------------------------------------
# RF626A
# ---------------------------------------------------------------------

RF626A_DERECHA = 105


def rf626a_cabecera(etiqueta="20022415821", area="REFRIGERADO", tienda="14196", fecha="6/10/26", pagina=1):
    return [
        _linea([(0, "SISTEMA .:  GETAFE"), (84, "LISTADO CONTENIDO POR FORMATO"), (169, f"Fecha ..: {fecha:>7}")]),
        _linea([(0, "CPD-RF626A"), (169, "Hora ...:  9:36:48")]),
        _linea([(0, "COD. ALMACEN ..........:   2  MAD GETAFE"), (169, f"Pagina .: {pagina:>5}")]),
        _linea([(0, "EMPRESA: DIA RETAIL"), (78, f"TIENDA/CONCESION..: {tienda}/00")]),
        "NUMERO DE ALBARAN ..........: 1-1567430",
        f"Area ...........:  {area:<40}PARTICION  ....:       1",
        f"CONTENEDOR  ....:  V0{' ' * 41}ETIQUETA   ....:   {etiqueta}",
    ]


def rf626a_columnas():
    titulos = [(5, "Codigo"), (14, "Descripcion"), (47, "Cantidad"), (56, "Formato"),
               (65, "Unidades"), (74, "Falta"), (80, "Otros"), (86, "Remonte")]
    guiones = [(5, "------"), (13, "-" * 32), (47, "--------"), (56, "-------"), (65, "--------"), (74, "-----")]
    piezas = titulos + [(c + RF626A_DERECHA, t) for c, t in titulos]
    piezas_g = guiones + [(c + RF626A_DERECHA, t) for c, t in guiones]
    return [_linea([(66, "Total"), (66 + RF626A_DERECHA, "Total")]), _linea(piezas), _linea(piezas_g)]


def _rf626a_lado(base, codigo, descripcion, cantidad, formato, unidades, col_formato=59):
    return [
        (base + 12 - len(codigo), codigo),
        (base + 13, descripcion),
        (base + 55 - len(str(cantidad)), str(cantidad)),
        (base + col_formato, formato or ""),
        (base + 73 - len(str(unidades)), str(unidades)),
    ]


def rf626a_fila(izq=None, der=None):
    """izq/der: (codigo, descripcion, cantidad, formato, unidades[, col_formato])."""
    piezas = []
    if izq:
        piezas += _rf626a_lado(0, *izq)
    if der:
        piezas += _rf626a_lado(RF626A_DERECHA, *der)
    return _linea(piezas)


def rf626a_totales(bultos="", unidades=""):
    return [
        f"          Total unidades ......           {unidades}",
        f"          Total bultos   ......           {bultos}",
        "          Total cajas    ......",
        _linea([(84, "Impreso en GETAFE")]),
    ]


def rf626a_resumen():
    """Página de cierre sin cabecera de columnas ni productos."""
    return [
        _linea([(0, "SISTEMA .:  GETAFE"), (84, "LISTADO CONTENIDO POR FORMATO")]),
        "CPD-RF626A",
        "          RESUMEN DE CONTENEDORES",
        "          Total bultos   ......           11",
        "          TOTAL UNIDADES .................           16",
        _linea([(87, "* * * FIN LISTADO * * *")]),
    ]


def rf626a_pagina(filas, **cabecera):
    return rf626a_cabecera(**cabecera) + rf626a_columnas() + filas + rf626a_totales()


# ---------------------------------------------------------------------
# RF625A
# ---------------------------------------------------------------------

RF625A_DERECHA = 93


def rf625a_cabecera(tienda="14196", fecha="14/01/26", area="UNITARIO ILLESCAS"):
    return [
        _linea([(0, "SISTEMA .:"), (84, "LISTADO CAJAS DE PREPARACION EN UNIDADES"), (169, f"Fecha ..: {fecha}")]),
        "CPD-RF625A",
        "COD. ALMACEN ..........:  73",
        _linea([(0, "EMPRESA: DIA RETAIL"), (78, f"TIENDA/CONCESION..: {tienda}/00")]),
        "NUMERO DE ALBARAN ..........: 0- 610268",
        f"Area ...........:  {area}",
        "Caja de plastico:  000000000000001498",
    ]


def rf625a_columnas():
    titulos = [(5, "Codigo"), (15, "Descripcion"), (61, "Cantidad"), (71, "Formato"), (80, "Falta"), (87, "Otros")]
    piezas = titulos + [(c + RF625A_DERECHA, t) for c, t in titulos]
    return [_linea(piezas)]


def _rf625a_lado(base, codigo, descripcion, cantidad, formato, col_formato=74):
    return [
        (base + 6, codigo),
        (base + 14, descripcion),
        (base + 69 - len(str(cantidad)), str(cantidad)),
        (base + col_formato, formato or ""),
    ]


def rf625a_fila(izq=None, der=None):
    piezas = []
    if izq:
        piezas += _rf625a_lado(0, *izq)
    if der:
        piezas += _rf625a_lado(RF625A_DERECHA, *der)
    return _linea(piezas)


def rf625a_pagina(filas, **cabecera):
    return rf625a_cabecera(**cabecera) + rf625a_columnas() + filas + [
        "          Total cantidad ......           16",
        _linea([(48, "TOTAL UNIDADES .................           16")]),
    ]
