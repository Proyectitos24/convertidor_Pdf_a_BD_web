"""
Huella lógica (determinista y versionada) del contenido de un .db
generado por el convertidor (batch_convert.py / cajas_azules.py).

Por qué no basta con el SHA-256 de los bytes del .db: los escritores
usan Linea.id INTEGER PRIMARY KEY AUTOINCREMENT y, dentro de un mismo
lote, pueden reabrir la misma ruta (DELETE + INSERT), así que el mismo
contenido lógico puede producir ids, sqlite_sequence, páginas libres o
cabeceras SQLite distintas. Dos archivos con los mismos datos deben
considerarse el mismo archivo aunque sus bytes difieran.

Qué entra en la huella (versión HUELLA_VERSION = "v1"):

  - Las tablas Etiqueta, Codigo, Descripcion, Linea y Metadata, en ese
    orden fijo. Si una tabla no existe, se registra explícitamente como
    ausente (distinto de "existe pero vacía").
  - Todas sus columnas reales (PRAGMA table_info), por nombre, EXCEPTO
    los alias de rowid (INTEGER PRIMARY KEY, p.ej. Linea.id), que son
    ids internos autoincrementales sin significado de negocio. Por eso
    Cantidad, Falta y las claves/valores de Metadata sí forman parte.
  - Cada valor se codifica con su tipo: NULL, entero, real, texto y
    blob nunca se confunden entre sí (NULL != '' != 0).
  - Las filas se ordenan por su codificación canónica, pero se
    conservan TODAS (lista ordenada, nunca set): dos filas idénticas
    cuentan dos veces.

No se incluye sqlite_sequence ni ninguna otra tabla interna. La huella
no altera el archivo: se abre siempre en modo de solo lectura sobre una
copia temporal, y el .db descargable sigue siendo exactamente los
bytes generados.
"""

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

HUELLA_VERSION = "v1"
HUELLA_PREFIJO = f"{HUELLA_VERSION}:"

TABLAS_HUELLA = ("Etiqueta", "Codigo", "Descripcion", "Linea", "Metadata")


class HuellaError(Exception):
    """No se pudo leer el .db para calcular su huella lógica."""


def _codificar_valor(valor):
    if valor is None:
        return ["n"]
    if isinstance(valor, bool):  # sqlite3 nunca lo devuelve; defensivo
        return ["i", str(int(valor))]
    if isinstance(valor, int):
        return ["i", str(valor)]
    if isinstance(valor, float):
        return ["r", repr(valor)]
    if isinstance(valor, str):
        return ["t", valor]
    if isinstance(valor, (bytes, bytearray, memoryview)):
        return ["b", bytes(valor).hex()]
    raise HuellaError(f"Tipo de valor SQLite no soportado: {type(valor).__name__}")


def _columnas_relevantes(cur, tabla):
    cur.execute(f'PRAGMA table_info("{tabla}")')
    info = cur.fetchall()  # (cid, name, type, notnull, dflt, pk)
    claves_pk = [fila for fila in info if fila[5]]
    columnas = []
    for _cid, nombre, tipo, _notnull, _dflt, pk in info:
        es_alias_rowid = (
            pk and len(claves_pk) == 1 and (tipo or "").strip().upper() == "INTEGER"
        )
        if not es_alias_rowid:
            columnas.append(nombre)
    return columnas


def _tabla_canonica(cur, tabla):
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (tabla,),
    )
    if cur.fetchone() is None:
        return {"tabla": tabla, "existe": False}

    columnas = sorted(_columnas_relevantes(cur, tabla))
    if columnas:
        lista = ", ".join(f'"{c}"' for c in columnas)
        cur.execute(f'SELECT {lista} FROM "{tabla}"')
        filas = [
            json.dumps([_codificar_valor(v) for v in fila], ensure_ascii=False, separators=(",", ":"))
            for fila in cur.fetchall()
        ]
    else:
        cur.execute(f'SELECT COUNT(*) FROM "{tabla}"')
        filas = ["[]"] * cur.fetchone()[0]

    filas.sort()  # orden determinista; se conservan los repetidos
    return {"tabla": tabla, "existe": True, "columnas": columnas, "filas": filas}


def huella_logica_de_ruta(db_path) -> str:
    """
    Calcula la huella de un .db en disco, abriéndolo con
    mode=ro (solo lectura): nunca escribe en el archivo.
    """
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            cur = conn.cursor()
            canon = [_tabla_canonica(cur, tabla) for tabla in TABLAS_HUELLA]
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise HuellaError(f"No se pudo leer el .db: {e}") from e

    serializado = json.dumps(
        {"version": HUELLA_VERSION, "tablas": canon},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return HUELLA_PREFIJO + hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def huella_logica_de_bytes(db_bytes: bytes) -> str:
    """
    Igual que huella_logica_de_ruta, sobre bytes en memoria: se vuelcan
    a un archivo temporal propio (los bytes originales no se tocan) y
    se abre en solo lectura. Lanza HuellaError si no es un SQLite
    legible — nunca devuelve una huella "vacía" por error.
    """
    if not db_bytes:
        raise HuellaError("El .db está vacío.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / "huella.db"
        tmp_path.write_bytes(bytes(db_bytes))
        return huella_logica_de_ruta(tmp_path)


def es_huella_actual(huella) -> bool:
    return isinstance(huella, str) and huella.startswith(HUELLA_PREFIJO) and len(huella) > len(HUELLA_PREFIJO)
