"""
Pruebas de services.db_metadata_reader.leer_grupo_de_db_bytes(). Construye
.db reales en disco temporal con sqlite3 puro — no se conecta a ningún
servicio.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from services.db_metadata_reader import leer_grupo_de_db_bytes
from services.grupo_packinglist import GRUPOS_VALIDOS, SIN_CLASIFICAR


def _fabricar_db_bytes(con_metadata=True, grupo="refrigerado", clave="grupo"):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "muestra.db"
        conn = sqlite3.connect(str(path))
        cur = conn.cursor()
        cur.execute("CREATE TABLE Etiqueta (Etiqueta TEXT PRIMARY KEY)")
        cur.execute("INSERT INTO Etiqueta VALUES ('20021563094')")

        if con_metadata:
            cur.execute(
                "CREATE TABLE Metadata (Clave TEXT PRIMARY KEY NOT NULL, Valor TEXT NOT NULL)"
            )
            cur.execute("INSERT INTO Metadata (Clave, Valor) VALUES (?, ?)", (clave, grupo))

        conn.commit()
        conn.close()

        return path.read_bytes()


class LeerGrupoDeDbBytesTests(unittest.TestCase):
    def test_grupo_valido_presente(self):
        data = _fabricar_db_bytes(grupo="congelado")
        self.assertEqual(leer_grupo_de_db_bytes(data), "congelado")

    def test_grupo_invalido_cae_a_sin_clasificar(self):
        data = _fabricar_db_bytes(grupo="no-es-un-grupo-valido")
        self.assertEqual(leer_grupo_de_db_bytes(data), SIN_CLASIFICAR)

    def test_sin_tabla_metadata_cae_a_sin_clasificar(self):
        data = _fabricar_db_bytes(con_metadata=False)
        self.assertEqual(leer_grupo_de_db_bytes(data), SIN_CLASIFICAR)

    def test_sin_clave_grupo_en_metadata_cae_a_sin_clasificar(self):
        data = _fabricar_db_bytes(clave="otra_clave", grupo="refrigerado")
        self.assertEqual(leer_grupo_de_db_bytes(data), SIN_CLASIFICAR)

    def test_bytes_corruptos_no_lanzan_excepcion(self):
        basura = b"esto no es una base de datos sqlite en absoluto"
        self.assertEqual(leer_grupo_de_db_bytes(basura), SIN_CLASIFICAR)

    def test_bytes_vacios(self):
        self.assertEqual(leer_grupo_de_db_bytes(b""), SIN_CLASIFICAR)
        self.assertEqual(leer_grupo_de_db_bytes(None), SIN_CLASIFICAR)

    def test_todos_los_grupos_validos_se_leen_correctamente(self):
        for grupo in GRUPOS_VALIDOS:
            with self.subTest(grupo=grupo):
                data = _fabricar_db_bytes(grupo=grupo)
                self.assertEqual(leer_grupo_de_db_bytes(data), grupo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
