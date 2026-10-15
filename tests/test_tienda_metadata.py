"""
Clave aditiva Metadata.tienda en los .db de los tres formatos admitidos
(RF626A, RF625A, AM613A). Sale SOLO de la cabecera TIENDA/CONCESION del
PDF: nunca del nombre del archivo ni del cuerpo. Si no es fiable (no
aparece o aparecen tiendas distintas), no se escribe.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

import batch_convert
import cajas_azules
from services import am613a_converter as am
from services import grupo_packinglist as grp
from tests import _am613a_sintetico as sx
from tests import _listados_sinteticos as ls


def _meta(ruta):
    conn = sqlite3.connect(ruta)
    try:
        return dict(conn.execute("SELECT Clave, Valor FROM Metadata"))
    finally:
        conn.close()


class TiendaDeCabeceraTests(unittest.TestCase):
    def test_tienda_unica_de_la_cabecera(self):
        self.assertEqual(grp.tienda_de_paginas(["EMPRESA: DIA  TIENDA/CONCESION..: 14140/00"]), "14140")
        self.assertEqual(grp.tienda_de_paginas(["TIENDA/CONCESION..: 14140/00", "TIENDA/CONCESION..: 14140/00"]), "14140")

    def test_sin_cabecera_o_tiendas_distintas_no_es_fiable(self):
        self.assertIsNone(grp.tienda_de_paginas([]))
        self.assertIsNone(grp.tienda_de_paginas(["packinglist_14196_2026-10-13.db", "TIENDA 14196"]))
        self.assertIsNone(grp.tienda_de_paginas(["TIENDA/CONCESION..: 14140/00", "TIENDA/CONCESION..: 14196/00"]))

    def test_validacion_del_codigo(self):
        self.assertEqual(grp.tienda_valida(" 14196 "), "14196")
        for malo in (None, "", "1419", "141960", "14x96", "../14196"):
            self.assertIsNone(grp.tienda_valida(malo), malo)

    def test_write_metadata_solo_anade_tienda_valida(self):
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        grp.write_metadata(cur, "seco", "Area=SECO", "RF626A", tienda="14140")
        self.assertEqual(dict(cur.execute("SELECT Clave, Valor FROM Metadata"))["tienda"], "14140")
        for malo in (None, "", "abc", "1419"):
            grp.write_metadata(cur, "seco", "Area=SECO", "RF626A", tienda=malo)
            self.assertNotIn("tienda", dict(cur.execute("SELECT Clave, Valor FROM Metadata")))
        conn.close()


class ConvertidoresTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_rf626a_tienda_de_cabecera_aunque_el_nombre_diga_otra(self):
        pagina = ls.rf626a_pagina([ls.rf626a_fila(("135285", "YOGUR GRIEGO", 1, "B", 8))], tienda="14140")
        pdf = ls.escribir_pdf(self.dir / "packinglist_14196_engañoso.pdf", [pagina])
        _t, _f, _p, dbs = batch_convert.process_pdf(pdf, self.dir / "salida")
        (db,) = dbs
        self.assertEqual(_meta(db)["tienda"], "14140")
        self.assertEqual(_meta(db)["schema_version"], "2", "clave aditiva: el esquema no cambia")

    def test_rf626a_con_tiendas_distintas_en_cabeceras_no_escribe_tienda(self):
        p1 = ls.rf626a_pagina([ls.rf626a_fila(("135285", "YOGUR GRIEGO", 1, "B", 8))], tienda="14140")
        p2 = ls.rf626a_pagina([ls.rf626a_fila(("13963", "LECHE ENTERA", 2, "U", 2))], tienda="14196", pagina=2)
        pdf = ls.escribir_pdf(self.dir / "rf626a.pdf", [p1, p2])
        _t, _f, _p, dbs = batch_convert.process_pdf(pdf, self.dir / "salida")
        for db in dbs:
            self.assertNotIn("tienda", _meta(db))

    def test_rf625a(self):
        pagina = ls.rf625a_pagina([ls.rf625a_fila(("135285", "YOGUR GRIEGO", 1, "B"))], tienda="14102")
        pdf = ls.escribir_pdf(self.dir / "cajas_azules_14196.pdf", [pagina])
        r = cajas_azules.process_pdf(pdf, self.dir / "salida")
        self.assertTrue(r["ok"], r)
        self.assertEqual(_meta(r["db_saved"])["tienda"], "14102")

    def test_am613a(self):
        ruta = self.dir / "AM613A_14140_engañoso.pdf"
        sx.escribir_pdf(ruta, sx.documento_basico(tienda="14196"))
        r = am.process_pdf(ruta, self.dir / "salida", "seco")
        self.assertEqual(_meta(r["db_saved"])["tienda"], "14196")


if __name__ == "__main__":
    unittest.main(verbosity=2)
