"""
Lectura de la tabla Metadata de un .db generado por el convertidor,
directamente desde bytes en memoria (nunca desde un .db versionado en
disco). La tabla SQLite es la fuente de verdad para el grupo operativo:
si no hay tabla Metadata, no hay clave 'grupo', o el valor no es uno de
los grupos canonicos, se trata como sin_clasificar.
"""

import sqlite3
import tempfile
from pathlib import Path

from services.grupo_packinglist import GRUPOS_VALIDOS, SIN_CLASIFICAR


def leer_grupo_de_db_bytes(db_bytes: bytes) -> str:
    """
    Devuelve el grupo canonico guardado en Metadata dentro de db_bytes,
    o SIN_CLASIFICAR si falta la tabla, la clave, o el valor no es
    reconocido. Nunca lanza excepcion: un .db corrupto o antiguo (sin
    Metadata) siempre cae a sin_clasificar.
    """
    if not db_bytes:
        return SIN_CLASIFICAR

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / "leido.db"
        tmp_path.write_bytes(db_bytes)

        try:
            conn = sqlite3.connect(str(tmp_path))
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='Metadata'"
                )
                if cur.fetchone() is None:
                    return SIN_CLASIFICAR

                cur.execute("SELECT Valor FROM Metadata WHERE Clave = 'grupo'")
                fila = cur.fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return SIN_CLASIFICAR

    if not fila or not fila[0]:
        return SIN_CLASIFICAR

    grupo = fila[0]
    return grupo if grupo in GRUPOS_VALIDOS else SIN_CLASIFICAR
