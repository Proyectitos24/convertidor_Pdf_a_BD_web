"""
Contrato compartido de la medida por línea (TipoMedida) de los .db que
genera el convertidor: AM613A, RF626A y RF625A/cajas azules.

  - Cada fila de Linea guarda TipoMedida = 'bulto' | 'unidad', y su
    Cantidad ya está expresada en esa medida (RepasoAlbaranes no hace
    conversiones).
  - La columna se añade al FINAL de Linea, así que la consulta existente
        SELECT Etiqueta, Codigo, Descripcion, Cantidad, Falta FROM Linea ORDER BY id
    sigue funcionando igual.

RF626A y RF625A: la medida sale SOLO del campo explícito "Formato" de
la misma línea de producto, por igualdad exacta (FORMATO_A_TIPO). Nunca
del código, la descripción, el grupo, el nombre del archivo ni el
parser. Evidencia del mapeo en los propios documentos: en todos los
RF626A disponibles la suma de Cantidad de las líneas con Formato "B"
coincide con el "Total bultos" impreso y la de "U" con "Total
unidades" (incluido un listado mixto 9 B / 132 U); en el RF625A la
suma de "U" coincide con "TOTAL UNIDADES" y "TOTAL BULTOS" está vacío.

S/P se conservan como semipale/pale. El parser RF626A solo normaliza a
una presentación los productos de PRODUCTOS_PRESENTACION_COMPLETA;
los demás S/P se rechazan, sin suponer que 60 bultos siempre son uno.
Cualquier valor ausente, desconocido o contradictorio sigue siendo error.
"""

import re
import sqlite3

TIPO_BULTO = "bulto"
TIPO_UNIDAD = "unidad"
TIPO_SEMIPALE = "semipale"
TIPO_PALE = "pale"
TIPOS_MEDIDA = (TIPO_BULTO, TIPO_UNIDAD, TIPO_SEMIPALE, TIPO_PALE)

# Presentaciones completas reconocidas por el convertidor histórico.
# No se infiere que cualquier cantidad S/P equivalga a una presentación.
PRODUCTOS_PRESENTACION_COMPLETA = frozenset({
    "26995", "26996", "263592", "608", "607", "504", "14752", "10626",
})

# Únicos valores del campo Formato con interpretación comprobada.
FORMATO_A_TIPO = {"B": TIPO_BULTO, "U": TIPO_UNIDAD,
                  "S": TIPO_SEMIPALE, "P": TIPO_PALE}

COLUMNA_TIPO_MEDIDA = "TipoMedida"

# Forma de una línea de producto y de un valor de Formato (una letra
# mayúscula, como todos los vistos: B, U) para detectar líneas que el
# parser descartaría en silencio.
_CODIGO_PRODUCTO_RE = re.compile(r"^\d{2,12}$")
_VALOR_FORMATO_RE = re.compile(r"^[A-Z]$")

LINEA_DDL = """
    CREATE TABLE IF NOT EXISTS Linea (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        Etiqueta TEXT NOT NULL,
        Codigo TEXT NOT NULL,
        Descripcion TEXT NOT NULL,
        Cantidad INTEGER NOT NULL,
        Falta INTEGER DEFAULT 0,
        TipoMedida TEXT NOT NULL CHECK (TipoMedida IN ('bulto', 'unidad', 'semipale', 'pale'))
    )
"""


class MedidaError(Exception):
    """Una o varias líneas sin medida fiable; no se genera ningún .db del documento."""

    def __init__(self, problemas):
        self.problemas = list(problemas)
        resumen = "; ".join(self.problemas[:8])
        if len(self.problemas) > 8:
            resumen += f"; … y {len(self.problemas) - 8} problema(s) más"
        super().__init__(resumen)


class EsquemaIncompatibleError(Exception):
    """Un .db existente en la ruta de salida no tiene el contrato con TipoMedida."""


def tipo_desde_formato(valores):
    """
    valores: textos leídos en la columna Formato de UNA línea (pueden
    repetirse por sobreimpresión en negrita). Devuelve (tipo, problema).
    """
    distintos = sorted(set(valores))
    if not distintos:
        return None, "sin Formato"
    if len(distintos) > 1:
        return None, f"Formato contradictorio ({', '.join(distintos)})"
    tipo = FORMATO_A_TIPO.get(distintos[0])
    if tipo is None:
        return None, f"Formato {distintos[0]!r} sin equivalencia comprobada"
    return tipo, None


def ensure_schema(cur):
    """Tablas del .db con TipoMedida. Si Linea ya existe sin TipoMedida, lanza EsquemaIncompatibleError."""
    # Primero se inspecciona (sin escribir nada) si ya hay una Linea antigua.
    cur.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'Linea'")
    if cur.fetchone() is not None:
        columnas = [fila[1] for fila in cur.execute("PRAGMA table_info(Linea)").fetchall()]
        if COLUMNA_TIPO_MEDIDA not in columnas:
            raise EsquemaIncompatibleError(
                "El .db de destino ya existe con el esquema antiguo (Linea sin TipoMedida); "
                "no se modifica ni se rellena. Usa otra carpeta de destino o retira ese archivo."
            )
    cur.execute("CREATE TABLE IF NOT EXISTS Etiqueta (Etiqueta TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS Codigo (Codigo TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS Descripcion (Descripcion TEXT PRIMARY KEY)")
    cur.execute(LINEA_DDL)


def comprobar_destino(out_path):
    """
    Comportamiento seguro al reutilizar una ruta SQLite existente:
    inspecciona su esquema SIN modificarlo y lanza
    EsquemaIncompatibleError si su Linea no tiene TipoMedida. Un .db
    inexistente o con el contrato actual no lanza nada.
    """
    from pathlib import Path

    ruta = Path(out_path)
    if not ruta.exists():
        return
    uri = ruta.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        tiene_linea = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'Linea'"
        ).fetchone()
        if tiene_linea is None:
            return
        columnas = [fila[1] for fila in conn.execute("PRAGMA table_info(Linea)").fetchall()]
    finally:
        conn.close()
    if COLUMNA_TIPO_MEDIDA not in columnas:
        raise EsquemaIncompatibleError(
            f"{ruta.name} ya existe con el esquema antiguo (Linea sin TipoMedida); "
            "no se modifica ni se rellena. Usa otra carpeta de destino o retira ese archivo."
        )


# ---------------------------------------------------------------------
# Lectura del campo Formato por coordenadas (RF626A / RF625A)
# ---------------------------------------------------------------------

def columnas_por_titulo(palabras, split_x, titulo):
    """
    Rango [x0, x1] de la cabecera de columna `titulo` en cada mitad de la
    página ({"izq": (x0, x1), "der": (x0, x1)}), a partir de las palabras
    de PyMuPDF. Una mitad sin esa cabecera no aparece en el resultado.
    """
    rangos = {}
    for x0, _y0, x1, _y1, texto, *_ in palabras:
        if texto != titulo:
            continue
        lado = "izq" if x0 < split_x else "der"
        rangos.setdefault(lado, (x0, x1))
    return rangos


def columnas_formato(palabras, split_x):
    """Rango de la columna "Formato" de cada mitad (ver columnas_por_titulo)."""
    return columnas_por_titulo(palabras, split_x, "Formato")


def columnas_cantidad(palabras, split_x):
    """Rango de la columna "Cantidad" de cada mitad (ver columnas_por_titulo)."""
    return columnas_por_titulo(palabras, split_x, "Cantidad")


def valores_en_columna(palabras_lado, rango, holgura=2.4):
    """Textos de las palabras cuyo centro cae en el rango (con media letra de holgura)."""
    if rango is None:
        return []
    x0, x1 = rango
    return [
        texto
        for px0, px1, texto in palabras_lado
        if x0 - holgura <= (px0 + px1) / 2 <= x1 + holgura
    ]


def tipo_de_linea(palabras_lado, rango):
    """
    (tipo, problema) de una línea de producto a partir de sus palabras
    [(x0, x1, texto)] de esa mitad de la página y del rango de la
    columna Formato. Las palabras sobreimpresas (negrita: mismo texto en
    la misma posición) cuentan una sola vez.
    """
    unicas = {(texto, round(x0, 1), round(x1, 1)): (x0, x1, texto) for x0, x1, texto in palabras_lado}
    return tipo_desde_formato(valores_en_columna(list(unicas.values()), rango))


def y_cabecera_formato(palabras):
    """Borde inferior (y1) de la cabecera "Formato" de la página, o None si no la hay."""
    ys = [y1 for _x0, _y0, _x1, y1, texto, *_ in palabras if texto == "Formato"]
    return max(ys) if ys else None


def problema_linea_no_reconocida(palabras_lado, rango, rango_cantidad=None):
    """
    Para una línea BAJO la cabecera de columnas que el parser del formato
    no reconoce como producto. Solo se considera artículo si tiene forma
    de línea de producto: empieza por un código numérico (los totales,
    pies y cabeceras empiezan por texto: "Total", "TOTAL", "Impreso"...).
    Entonces:
      - con un valor de una letra en la columna Formato que el parser no
        admite -> se informa ese Formato;
      - con la columna Formato completamente vacía y un número en la
        columna Cantidad -> se informa que no puede determinarse su
        medida.
    En otro caso devuelve None. Evita que un artículo se descarte en
    silencio sin inventar su TipoMedida.
    """
    ordenadas = sorted(palabras_lado, key=lambda p: p[0])
    if not ordenadas or not _CODIGO_PRODUCTO_RE.match(ordenadas[0][2]):
        return None  # no tiene forma de línea de producto (totales, pies, cabeceras)
    unicas = list({(texto, round(x0, 1), round(x1, 1)): (x0, x1, texto) for x0, x1, texto in ordenadas}.values())
    en_formato = valores_en_columna(unicas, rango)
    valores = [v for v in en_formato if _VALOR_FORMATO_RE.match(v)]
    if valores:
        return f"línea no reconocida con Formato {', '.join(sorted(set(valores)))!r}"
    if rango is not None and not en_formato:
        cantidades = [v for v in valores_en_columna(unicas, rango_cantidad) if v.isdigit()]
        if cantidades:
            return "sin Formato: no puede determinarse su medida"
    return None
