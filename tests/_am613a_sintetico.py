"""
Generador de PDFs AM613A sintéticos para pruebas portables (no es una
prueba en sí). Reproduce la disposición monoespaciada de los albaranes
reales: Courier 8 pt (4,8 pt por carácter, igual que el listado real),
cabecera repetida en cada página, línea de guiones que delimita las
columnas, líneas de Totales por partición y secciones de incidencias,
auditoría y restos.

Todo se escribe en la ruta que indique la prueba (siempre dentro de un
TemporaryDirectory). Nunca se usan los PDFs reales de Downloads.
"""

import fitz

ALTO_LINEA = 12
MARGEN_SUPERIOR = 20
FUENTE = "cour"
TAM = 8


def _linea_en_columnas(piezas):
    """piezas: [(columna_inicio, texto)] -> línea con cada texto en su columna."""
    ancho = max(c + len(t) for c, t in piezas)
    buf = [" "] * ancho
    for c, t in piezas:
        buf[c:c + len(t)] = list(t)
    return "".join(buf).rstrip()


def cabecera(albaran="7- 252345", tienda="14196", fecha="2/10/26", pagina=1, identificador="CPD-AM613A"):
    return [
        f"SISTEMA .: GETAFE{' ' * 67}ALBARAN  DE  ENTREGA{' ' * 74}Fecha ..:  {fecha:>7}",
        f"{identificador}{' ' * 74}--------------------{' ' * 74}Hora ...:   9:27:44",
        f"ALM. PREPARACION:  73  ILLESCAS CENTRAL{' ' * 40}Pagina .:  {pagina:>7}",
        f"DIRECCION DE ALMACEN : CTRA-NACIONAL-IV, KM.12{' ' * 42}TIENDA/CONCESION..: {tienda} /",
        f"NUMERO DE ALBARAN ..........: {albaran:<26}NUMERO DE PEDIDO .........:  252345",
    ]


def particion(numero):
    return [f"PARTICION NUMERO............:  {numero:>5}"]


def cabecera_columnas():
    return [
        _linea_en_columnas([(56, "Und."), (63, "Total")]),
        _linea_en_columnas([
            (20, "Descripcion"), (43, "F"), (45, "Bultos"), (52, "UXB"), (56, "Suel"),
            (62, "Unidades"), (72, "Bultos"), (81, "Bruto"), (89, "Line"),
        ]),
        "   Codigo",
        "   ------ -------------------------------- - ------ --- ---- --------- -------- -------- ----  --------",
    ]


def articulo(codigo, descripcion, bultos="", uxb="", suel="", total="", linea=1, precio="1,00"):
    def v(x):
        return "" if x is None else str(x)

    return (
        f"{codigo:>9} {descripcion:<32.32}   {v(bultos):>6} {v(uxb):>3} {v(suel):>4} {v(total):>9}"
        f" {'':8} {'':8} {linea:>4}  {precio:>8}{' ' * 20}21,000 16,000"
    )


def totales(bultos, suel, total):
    return [
        "                                             ------   ------ ---------",
        "                        Totales",
        "                        Totales ........:",
        f"{' ' * 32}........:{bultos:>10}{suel:>9}{total:>10}",
    ]


def total_pedido(bultos, unidades):
    return [
        f"  TOTAL PEDIDO   :  {bultos:>8}   {unidades:>8}          /                 3.181,73",
        f"  MATERIAL AUX.  :  {4:>8}   {0:>8}          /                     0,00",
        "                                           TIENDA 14196/00",
    ]


def incidencias(filas=(("303436", 1, "B", "BONITO NORTE OLIVA MARIM 140 G", "082", "ART. DE BAJA"),)):
    out = [
        "                                *** INCIDENCIAS EN PEDIDO ***",
        "                                -----------------------------",
        "  Cod.      Cant.",
        "  Artic.    Pedida.    Fmt.",
        "  ------    -------    ----    --------------------------------    -----     ----------------",
    ]
    for cod, cant, fmt, desc, inc, texto in filas:
        out.append(f"  {cod:<6}    {cant:>7}     {fmt}     {desc:<32}    {inc:>5}     {texto}")
    return out


def auditoria(filas=(("145652", "YOGURES DE FRUTAS 0% DIALA 6X125", 1, 1, 0),)):
    out = [
        "                                *** AUDITORIA DE PEDIDO DE REFRIGERADO ALTA ROT",
        "                                -----------------------------------------------",
        "  Cod.                                                Cant.      Cant.      Cant.",
        "                 Descripcion Articulo         Fmt.    Pedida    Asignada   Servida",
        "  ------   --------------------------------   ----   --------   --------   --------",
    ]
    for cod, desc, ped, asig, serv in filas:
        out.append(f"  {cod:<6}   {desc:<32}    B   {ped:>8}   {asig:>8}   {serv:>8}    Falta de Stock")
    return out


def restos(filas=(("222222", "ARTICULO RESTO", 1, 6, 6),)):
    out = [
        "                        Articulos que se han quedado como restos",
        "                       ------------------------------------------",
        "  Cod.                                                                Total       Total",
        "                 Descripcion Articulo        Fmt  Bultos  UXB        Unidades    Formatos",
        "  ------   --------------------------------  ---  ------  -----  --------------  ---------",
    ]
    for cod, desc, b, uxb, t in filas:
        out.append(f"  {cod:<6}   {desc:<32}  B   {b:>6}  {uxb:>5}  {t:>14}  {b:>9}")
    return out


def pie():
    return ["                                                                                    Impreso en GETAFE"]


def escribir_pdf(ruta, paginas):
    """paginas: lista de listas de líneas de texto (una lista por página)."""
    doc = fitz.open()
    for lineas in paginas:
        page = doc.new_page(width=950, height=792)
        for i, linea in enumerate(lineas):
            if linea:
                page.insert_text((0, MARGEN_SUPERIOR + i * ALTO_LINEA), linea, fontname=FUENTE, fontsize=TAM)
    doc.save(str(ruta))
    doc.close()
    return ruta


def documento_basico(albaran="7- 252345", tienda="14196", fecha="2/10/26"):
    """
    Albarán válido de 3 páginas con dos particiones y dos bloques en la
    página 1 (fin de la partición 1 e inicio de la 2), AUX e I/O,
    incidencias, auditoría y restos. Artículos esperados (en orden):
      283092 unidad 4, 6649 unidad 1, 299121 bulto 2, 224028 bulto 3,
      530 bulto 1, 48710 bulto 1.
    """
    def cab(n):
        return cabecera(albaran=albaran, tienda=tienda, fecha=fecha, pagina=n)

    pagina1 = (
        cab(1) + particion(1) + cabecera_columnas()
        + [
            articulo("283092", "TABLET LIMPIAD PROTE BOTIK 30UDS", 0, 12, 4, 4),
            articulo("6649", "PROTECTOR LAB ORIGIN LIPOS 1 UD", 0, 24, 1, 1),
            articulo("AUX721413", "GUANT NITRIL NEGRO M", 0, 10, 1, 1),
        ]
        + totales(0, 6, 6)
        + particion(2) + cabecera_columnas()
        + [
            articulo("299121", "AJO EN POLVO VEGEC 85 G", 2, 8, "", 16),
            articulo("AUX721520", "FUNDA PAN DIANA", 2, 1, "", 2),
        ]
        + pie()
    )
    pagina2 = (
        cab(2) + particion(2) + cabecera_columnas()
        + [
            articulo("224028", "PANDORO DE CHOCOLATE ELMOL 750 G", 3, 6, "", 18),
            articulo("I/O312634", "ARTICULO I/O", 1, 6, "", 6),
            articulo("530", "SOPA DE POLLO FIDEOS GBLAN 71 G", 1, 24, "", 24),
            articulo("48710", "GINEBRA 37,5 BARMO 70 CL", 1, 6, "", 6),
        ]
        + totales(10, 0, 72)
        + total_pedido(10, 6)
        + pie()
    )
    pagina3 = cab(3) + incidencias() + auditoria() + restos() + pie()
    return [pagina1, pagina2, pagina3]
