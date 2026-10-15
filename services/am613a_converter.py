"""
Conversor de albaranes de entrega AM613A ("ALBARAN DE ENTREGA",
cabecera CPD-AM613A) a un único .db por albarán.

Separado a propósito de batch_convert.py (RF626A) y cajas_azules.py
(RF625A): no comparte parser ni modifica sus resultados. Nunca mueve ni
escribe el PDF de entrada: solo lo abre para leerlo.

EXTRACCIÓN (PyMuPDF, por coordenadas)
  El listado es texto monoespaciado. Bajo la cabecera de columnas
  ("Descripcion F Bultos UXB Suel Unidades ...") hay una línea de guiones
  cuyos segmentos delimitan exactamente cada columna; se usan como
  límites para asignar cada palabra a su columna. Cada página repite la
  cabecera, y una misma página puede contener el final de una partición
  y el inicio de otra, por eso se procesa como máquina de estados:
  "PARTICION NUMERO" fija la partición actual, la línea de guiones abre
  la tabla de artículos y cualquier sección distinta (Totales,
  INCIDENCIAS EN PEDIDO, AUDITORIA DE PEDIDO, Articulos que se han
  quedado como restos, TOTAL PEDIDO, pie de página) la cierra.

  Cada partición se valida contra su línea "Totales" (Bultos, Und.
  Suel., Total Unidades), que en los albaranes reales incluye también
  las líneas de material auxiliar. Si no cuadra o falta, el documento
  se rechaza entero: nunca se genera un .db parcial.

CANTIDAD Y TIPO DE MEDIDA
  - Bultos > 0 y Und. Suel. vacío/0  -> Cantidad = Bultos, "bulto".
    (Se comprueba Total Unidades = Bultos x UXB.)
  - Bultos vacío/0 y Und. Suel. > 0  -> Cantidad = Total Unidades,
    "unidad". (Se comprueba Total Unidades = Und. Suel.)
  - Cualquier otro caso (ambos positivos, ambos vacíos, ilegibles o
    incoherentes) es un error del documento.
  UXB nunca es Cantidad y bultos y unidades nunca se suman.

  Las líneas cuyo código empieza por "AUX" o "I/O" (material auxiliar)
  son artículos como cualquier otro: mismas reglas de Cantidad/
  TipoMedida, nunca se eliminan ni se fusionan con otra línea distinta.
  Al guardar su Codigo se les quita EXCLUSIVAMENTE ese prefijo
  ("AUX721413" -> "721413", "I/O312634" -> "312634"), conservando los
  ceros iniciales; no se aplica ningún otro recorte genérico, y un
  código ya numérico no se toca. Si el código resultante coincide con
  un código numérico YA presente en el mismo documento, el documento se
  rechaza entero (nunca se guardan dos artículos distintos bajo el
  mismo Codigo sin que alguien lo revise).

CONTRATO SQLITE (aditivo, compatible con la consulta existente
  SELECT Etiqueta, Codigo, Descripcion, Cantidad, Falta FROM Linea ORDER BY id)
  - Mismas tablas Etiqueta, Codigo, Descripcion, Linea, Metadata.
  - Linea añade al final la columna TipoMedida ('bulto' | 'unidad').
    Solo existe en los .db AM613A; RF626A/RF625A no cambian.
  - Etiqueta sintética "AM613A_<tienda>_<albarán>": el AM613A no tiene
    etiqueta física de palet; el prefijo deja claro que es un
    identificador de documento, determinista por tienda y albarán.
  - Falta = 0 (misma semántica actual: las incidencias no la rellenan).
  - Metadata: schema_version actual ("2", el contrato de las 4 claves no
    cambia y RepasoAlbaranes solo confía en grupo con "2"), grupo
    elegido manualmente, grupo_fuente = MANUAL, formato_pdf = AM613A.

NOMBRE DEL .db
  "<albarán normalizado>_<fecha del PDF AAAA-MM-DD>.db", p.ej.
  "7-252345_2026-10-02.db". La fecha es necesaria porque RepasoAlbaranes
  la extrae del nombre del archivo al importar.

AM613A frente a RF626A: un mismo envío puede llegar en ambos formatos.
No hay deduplicación cruzada (nombres y organización distintos: por
albarán frente a por etiqueta de palet); son alternativas, e importar
ambos en RepasoAlbaranes podría duplicar el repaso.
"""

import re
import sqlite3
from datetime import datetime
from pathlib import Path

import fitz

from services import grupo_packinglist as grp
from services.pdf_format import AM613A
from services.tipo_medida import TIPO_BULTO, TIPO_UNIDAD
from services.tipo_medida import ensure_schema as _ensure_schema_tipo_medida

FUENTE_MANUAL = "MANUAL"
# TIPO_BULTO / TIPO_UNIDAD y el esquema de Linea con TipoMedida vienen
# del contrato compartido services/tipo_medida.py (también RF626A/RF625A).

TOL_Y = 2.0

_TIENDA_RE = re.compile(r"TIENDA/CONCESION\.*:\s*(\d{5})")
_TIENDA_PIE_RE = re.compile(r"\bTIENDA\s+(\d{5})/\d{2}\b")
_FECHA_RE = re.compile(r"Fecha\s*\.*:\s*(\d{1,2})/(\d{1,2})/(\d{2})\b")
_ALBARAN_LINEA_RE = re.compile(r"NUMERO DE ALBARAN\s*\.*:[ \t]*([^\n]*)")
_ALBARAN_RE = re.compile(r"^(\d+)\s*-\s*(\d+)\b")
_PARTICION_RE = re.compile(r"PARTICION NUMERO\.*:\s*(\d+)")
_ENTERO_RE = re.compile(r"^(\d{1,3}(\.\d{3})+|\d+)$")
# Numérico, o material auxiliar con su prefijo completo (AUX.../I-O...).
_CODIGO_RE = re.compile(r"^(?:\d+|AUX\d+|I/O\d+)$")
# Reconoce ÚNICAMENTE el prefijo "AUX" o "I/O" seguido solo de dígitos —
# nunca un recorte genérico de caracteres. Se usa tanto para normalizar
# el Codigo guardado como para detectar colisiones con un código numérico
# ya presente en el documento.
_CODIGO_AUX_IO_RE = re.compile(r"^(?:AUX|I/O)(\d+)$")
_NOMBRE_DB_RE = re.compile(r"^\d+-\d+_\d{4}-\d{2}-\d{2}\.db$")

# Cualquiera de estos textos en una fila cierra la tabla de artículos.
_FIN_DE_TABLA = (
    "TOTALES",
    "TOTAL PEDIDO",
    "INCIDENCIAS EN PEDIDO",
    "AUDITORIA DE PEDIDO",
    "AUDITORÍA DE PEDIDO",
    "QUEDADO COMO RESTOS",
    "FIN LISTADO",
    "IMPRESO EN",
    "MATERIAL AUX",
)

_COLUMNAS = ("codigo", "descripcion", "f", "bultos", "uxb", "suel", "total")
_CABECERA_COLUMNAS = {"bultos": "Bultos", "uxb": "UXB", "suel": "Suel", "total": "Unidades"}


class Am613aError(Exception):
    """El documento no se puede convertir de forma fiable; no se genera nada."""

    def __init__(self, problemas):
        self.problemas = list(problemas)
        resumen = "; ".join(self.problemas[:8])
        if len(self.problemas) > 8:
            resumen += f"; … y {len(self.problemas) - 8} problema(s) más"
        super().__init__(resumen)


# ---------------------------------------------------------------------
# Cabecera del documento
# ---------------------------------------------------------------------

def _valor_unico(valores, nombre, problemas):
    distintos = sorted(set(valores))
    if not distintos:
        problemas.append(f"No se encontró {nombre} en el documento")
        return None
    if len(distintos) > 1:
        problemas.append(f"{nombre} contradictorio en el documento: {', '.join(distintos)}")
        return None
    return distintos[0]


def normalizar_albaran(texto):
    """'7- 252345' -> '7-252345'. Devuelve None si no tiene la forma <dígitos>-<dígitos>."""
    m = _ALBARAN_RE.match((texto or "").strip())
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}"


def extraer_cabecera(textos_paginas):
    problemas = []
    tiendas, fechas, albaranes = [], [], []

    for n, texto in enumerate(textos_paginas, start=1):
        tiendas += _TIENDA_RE.findall(texto)
        tiendas += _TIENDA_PIE_RE.findall(texto)

        for d, mo, yy in _FECHA_RE.findall(texto):
            try:
                fechas.append(datetime(2000 + int(yy), int(mo), int(d)).strftime("%Y-%m-%d"))
            except ValueError:
                problemas.append(f"Fecha inválida en la página {n}: {d}/{mo}/{yy}")

        for resto in _ALBARAN_LINEA_RE.findall(texto):
            albaran = normalizar_albaran(resto)
            if albaran is None:
                problemas.append(f"Número de albarán ilegible en la página {n}: {resto.strip()[:30]!r}")
            else:
                albaranes.append(albaran)

    tienda = _valor_unico(tiendas, "la tienda", problemas)
    fecha = _valor_unico(fechas, "la fecha", problemas)
    albaran = _valor_unico(albaranes, "el número de albarán", problemas)
    return tienda, fecha, albaran, problemas


# ---------------------------------------------------------------------
# Tabla de artículos por coordenadas
# ---------------------------------------------------------------------

def _filas(page):
    palabras = sorted(page.get_text("words"), key=lambda w: (w[1], w[0]))
    filas, actual, y_actual = [], [], None
    for w in palabras:
        if y_actual is None or w[1] - y_actual > TOL_Y:
            if actual:
                filas.append(sorted(actual, key=lambda p: p[0]))
            actual, y_actual = [], w[1]
        actual.append(w)
    if actual:
        filas.append(sorted(actual, key=lambda p: p[0]))
    return filas


def _es_guiones(palabra):
    return bool(palabra) and set(palabra) == {"-"}


def _columnas_desde_guiones(fila_guiones, fila_cabecera):
    """Límites [x0, x1] de cada columna a partir de los segmentos de guiones."""
    segmentos = [(w[0], w[2]) for w in fila_guiones]
    if len(segmentos) < len(_COLUMNAS):
        return None
    columnas = dict(zip(_COLUMNAS, segmentos))

    # La cabecera impresa debe caer dentro de su segmento: si el diseño
    # del listado cambia, se rechaza en lugar de leer otra columna.
    for clave, titulo in _CABECERA_COLUMNAS.items():
        x0, x1 = columnas[clave]
        if not any(w[4] == titulo and x0 - 0.5 <= w[0] and w[2] <= x1 + 0.5 for w in fila_cabecera):
            return None
    return columnas


def _asignar_columnas(fila, columnas):
    """Devuelve ({columna: [palabras]}, [palabras sin columna]) dentro del área de la tabla."""
    ancho_car = 4.8
    asignadas = {c: [] for c in _COLUMNAS}
    huerfanas = []
    limite_derecho = columnas["total"][1] + ancho_car / 2
    for w in fila:
        centro = (w[0] + w[2]) / 2
        if centro > limite_derecho:
            continue  # pesos, precios e impuestos: fuera del alcance
        for c in _COLUMNAS:
            x0, x1 = columnas[c]
            if c == "codigo":
                x0 = 0.0  # los códigos AUX/I/O se extienden a la izquierda
            if x0 - ancho_car / 2 <= centro <= x1 + ancho_car / 2:
                asignadas[c].append(w[4])
                break
        else:
            huerfanas.append(w[4])
    return asignadas, huerfanas


def _entero(palabras, columna, problemas, contexto):
    if not palabras:
        return None
    if len(palabras) > 1 or not _ENTERO_RE.match(palabras[0]):
        problemas.append(f"{contexto}: valor ilegible en {columna}: {' '.join(palabras)!r}")
        return "ilegible"
    return int(palabras[0].replace(".", ""))


def _texto_fila(fila):
    return " ".join(w[4] for w in fila)


def _es_cabecera_articulos(fila):
    textos = {w[4] for w in fila}
    return {"Descripcion", "Bultos", "UXB", "Suel", "Unidades"} <= textos


def _leer_totales(fila, columnas):
    asignadas, _ = _asignar_columnas(fila, columnas)
    valores = []
    for c in ("bultos", "suel", "total"):
        p = asignadas[c]
        if len(p) != 1 or not _ENTERO_RE.match(p[0]):
            return None
        valores.append(int(p[0].replace(".", "")))
    return tuple(valores)


def extraer_lineas(doc):
    """
    Recorre todas las páginas y devuelve (lineas, problemas).

    lineas: dicts con codigo, descripcion, bultos, uxb, suel, total,
    pagina, particion.
    """
    problemas = []
    lineas = []
    totales = {}
    particion = None
    columnas = None

    for page in doc:
        n_pag = page.number + 1
        estado = "fuera"
        cabecera = None

        for fila in _filas(page):
            texto = _texto_fila(fila)
            mayus = texto.upper()

            m = _PARTICION_RE.search(texto)
            if m:
                particion = int(m.group(1))
                estado = "fuera"
                continue

            if _es_cabecera_articulos(fila):
                cabecera = fila
                estado = "cabecera"
                continue

            if estado == "cabecera" and all(_es_guiones(w[4]) for w in fila):
                columnas = _columnas_desde_guiones(fila, cabecera)
                if columnas is None:
                    problemas.append(f"Página {n_pag}: columnas de artículos con un diseño inesperado")
                    estado = "fuera"
                elif particion is None:
                    problemas.append(f"Página {n_pag}: tabla de artículos sin número de partición")
                    estado = "fuera"
                else:
                    estado = "articulos"
                continue

            if estado == "articulos":
                if all(_es_guiones(w[4]) for w in fila) or any(t in mayus for t in _FIN_DE_TABLA):
                    estado = "totales" if (all(_es_guiones(w[4]) for w in fila) or "TOTALES" in mayus) else "fuera"
                    continue
                linea = _leer_linea(fila, columnas, n_pag, particion, problemas)
                if linea is not None:
                    lineas.append(linea)
                continue

            if estado == "totales":
                if "........:" in texto and columnas is not None:
                    valores = _leer_totales(fila, columnas)
                    if valores is not None:
                        previo = totales.get(particion)
                        if previo is not None and previo != valores:
                            problemas.append(f"Partición {particion}: totales contradictorios {previo} y {valores}")
                        totales[particion] = valores
                        estado = "fuera"
                continue

    _validar_totales(lineas, totales, problemas)
    return lineas, problemas


def _leer_linea(fila, columnas, n_pag, particion, problemas):
    asignadas, huerfanas = _asignar_columnas(fila, columnas)
    contexto = f"Página {n_pag}"

    codigos = asignadas["codigo"]
    if len(codigos) != 1:
        problemas.append(f"{contexto}: fila no reconocida en la tabla de artículos: {_texto_fila(fila)[:60]!r}")
        return None
    codigo = codigos[0]
    contexto = f"Página {n_pag}, código {codigo}"

    if not _CODIGO_RE.match(codigo):
        problemas.append(f"{contexto}: código no numérico")
        return None
    if huerfanas:
        problemas.append(f"{contexto}: texto fuera de columna: {' '.join(huerfanas)!r}")
        return None

    descripcion = " ".join(asignadas["descripcion"]).strip()
    if not descripcion:
        problemas.append(f"{contexto}: sin descripción")
        return None

    problemas_linea = []
    valores = {
        c: _entero(asignadas[c], c, problemas_linea, contexto)
        for c in ("bultos", "uxb", "suel", "total")
    }
    if problemas_linea:
        problemas.extend(problemas_linea)
        return None

    return {
        "codigo": codigo,
        "descripcion": descripcion,
        "pagina": n_pag,
        "particion": particion,
        **valores,
    }


def _validar_totales(lineas, totales, problemas):
    particiones = sorted({l["particion"] for l in lineas} | set(totales))
    for p in particiones:
        de_p = [l for l in lineas if l["particion"] == p]
        if p not in totales:
            if de_p:
                problemas.append(f"Partición {p}: no se encontraron sus totales; no se puede verificar")
            continue
        suma = tuple(sum(l[c] or 0 for l in de_p) for c in ("bultos", "suel", "total"))
        if suma != totales[p]:
            problemas.append(
                f"Partición {p}: las líneas suman (bultos, sueltas, unidades) = {suma} "
                f"pero el documento indica {totales[p]}"
            )


def cantidad_y_tipo(linea):
    """
    (cantidad, tipo, problema). problema es None si la línea es válida.
    """
    b, s, t, uxb = linea["bultos"], linea["suel"], linea["total"], linea["uxb"]
    b0 = b or 0
    s0 = s or 0

    if b0 > 0 and s0 > 0:
        return None, None, "tiene bultos y unidades sueltas a la vez"
    if b0 > 0:
        if t is not None and uxb is not None and t != b0 * uxb:
            return None, None, f"Total Unidades {t} no coincide con Bultos {b0} x UXB {uxb}"
        return b0, TIPO_BULTO, None
    if s0 > 0:
        if t is None:
            return None, None, "unidades sueltas sin Total Unidades"
        if t != s0:
            return None, None, f"Total Unidades {t} no coincide con Und. Suel. {s0}"
        return t, TIPO_UNIDAD, None
    return None, None, "sin bultos ni unidades sueltas"


def _normalizar_codigo(codigo):
    """
    Quita el prefijo "AUX" o "I/O" de un código de material auxiliar,
    dejando solo los dígitos (con sus posibles ceros iniciales). Un
    código ya numérico se devuelve sin cambios — nunca se aplica un
    recorte genérico de caracteres, solo estos dos prefijos reconocidos.
    """
    m = _CODIGO_AUX_IO_RE.match(codigo)
    return m.group(1) if m else codigo


def _validar_colisiones_codigo(lineas, problemas):
    """
    Si, al quitar el prefijo de material auxiliar, dos códigos
    ORIGINALMENTE distintos del documento (p.ej. "721413" y
    "AUX721413") acaban coincidiendo, se rechaza el documento entero:
    nunca se guardan dos artículos distintos bajo el mismo Codigo sin
    que alguien lo revise.
    """
    originales_por_normalizado = {}
    for l in lineas:
        normalizado = _normalizar_codigo(l["codigo"])
        originales_por_normalizado.setdefault(normalizado, set()).add(l["codigo"])
    for normalizado, originales in originales_por_normalizado.items():
        if len(originales) > 1:
            problemas.append(
                f"Los códigos {', '.join(sorted(originales))} coinciden en {normalizado!r} al quitar el "
                f"prefijo de material auxiliar; no se puede guardar sin ambigüedad"
            )


def extraer_documento(doc):
    """
    Extrae y valida todo el documento. Devuelve un dict con tienda,
    fecha, albaran, etiqueta, nombre_db y articulos (incluye el
    material auxiliar AUX/I-O, con el prefijo quitado de su código —
    ver _normalizar_codigo). Lanza Am613aError con TODOS los problemas
    encontrados: nunca devuelve un resultado parcial.
    """
    textos = [page.get_text("text") or "" for page in doc]
    tienda, fecha, albaran, problemas = extraer_cabecera(textos)

    lineas, problemas_lineas = extraer_lineas(doc)
    problemas += problemas_lineas
    _validar_colisiones_codigo(lineas, problemas)

    articulos = []
    for linea in lineas:
        cantidad, tipo, problema = cantidad_y_tipo(linea)
        if problema:
            problemas.append(f"Página {linea['pagina']}, código {linea['codigo']}: {problema}")
            continue
        articulos.append(
            {"codigo": _normalizar_codigo(linea["codigo"]), "descripcion": linea["descripcion"], "cantidad": cantidad, "tipo": tipo}
        )

    if not articulos and not problemas:
        problemas.append("No se encontró ningún artículo en el documento")

    if problemas:
        raise Am613aError(problemas)

    nombre_db = f"{albaran}_{fecha}.db"
    if not _NOMBRE_DB_RE.match(nombre_db):  # defensivo: nunca un nombre inesperado
        raise Am613aError([f"Nombre de archivo inválido: {nombre_db!r}"])

    return {
        "tienda": tienda,
        "fecha": fecha,
        "albaran": albaran,
        "etiqueta": etiqueta_sintetica(tienda, albaran),
        "nombre_db": nombre_db,
        "articulos": articulos,
    }


def etiqueta_sintetica(tienda, albaran):
    return f"{AM613A}_{tienda}_{albaran}"


# ---------------------------------------------------------------------
# Escritura SQLite
# ---------------------------------------------------------------------

def ensure_schema(cur):
    _ensure_schema_tipo_medida(cur)


def agrupar_articulos(articulos):
    """
    Suma la cantidad de un mismo (código, descripción, tipo de medida),
    conservando el orden de primera aparición. Bultos y unidades de un
    mismo código quedan en filas separadas: nunca se suman entre sí.
    """
    acc = {}
    for a in articulos:
        clave = (a["codigo"], a["descripcion"], a["tipo"])
        acc[clave] = acc.get(clave, 0) + a["cantidad"]
    return [
        {"codigo": c, "descripcion": d, "tipo": t, "cantidad": q}
        for (c, d, t), q in acc.items()
    ]


def write_db(etiqueta, articulos, out_path, grupo, tienda=None):
    out_path = Path(out_path)
    if out_path.exists():
        raise FileExistsError(f"Ya existe {out_path.name}; no se sobrescribe")

    conn = sqlite3.connect(out_path)
    try:
        cur = conn.cursor()
        ensure_schema(cur)
        cur.execute("INSERT INTO Etiqueta (Etiqueta) VALUES (?)", (etiqueta,))
        for a in agrupar_articulos(articulos):
            cur.execute("INSERT OR IGNORE INTO Codigo (Codigo) VALUES (?)", (a["codigo"],))
            cur.execute("INSERT OR IGNORE INTO Descripcion (Descripcion) VALUES (?)", (a["descripcion"],))
            cur.execute(
                "INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad, Falta, TipoMedida) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (etiqueta, a["codigo"], a["descripcion"], a["cantidad"], a["tipo"]),
            )
        grp.write_metadata(cur, grupo, FUENTE_MANUAL, AM613A, tienda=tienda)
        conn.commit()
    finally:
        conn.close()


def process_pdf(pdf_path, out_root, grupo):
    """
    Convierte un AM613A. Lee el PDF sin moverlo ni modificarlo y escribe
    un único .db en out_root. Lanza Am613aError si el grupo no es válido
    o el documento no se puede convertir de forma fiable.
    """
    if grupo not in grp.GRUPOS_VALIDOS:
        raise Am613aError([f"Grupo no válido o no elegido: {grupo!r}"])

    doc = fitz.open(str(pdf_path))
    try:
        datos = extraer_documento(doc)
    finally:
        doc.close()

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    out_db = out_root / datos["nombre_db"]
    # La tienda ya está validada como única en extraer_cabecera.
    write_db(datos["etiqueta"], datos["articulos"], out_db, grupo, tienda=datos["tienda"])

    return {**datos, "db_saved": str(out_db)}
