"""Regresión local opcional: el PDF del usuario no se versiona ni se modifica."""
import hashlib
from contextlib import closing
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest

from services.conversion_service import convert_uploaded_files

PDF_LOCAL = Path("C:/Users/migue/Downloads/albaranes prueba/RF626A____20261013035739727270.pdf")


class SemipalesPdfRealTests(unittest.TestCase):
    @unittest.skipUnless(PDF_LOCAL.is_file(), "muestra privada no disponible")
    def test_conversion_web_conserva_semipale_y_bultos_del_pdf_real(self):
        original = PDF_LOCAL.read_bytes()
        subida = io.BytesIO(original)
        subida.name = PDF_LOCAL.name
        generados, resumen = convert_uploaded_files([subida])
        self.assertTrue(generados, resumen)
        self.assertTrue(all(r["estado"] == "ok" for r in resumen), resumen)
        leche = []
        with tempfile.TemporaryDirectory() as carpeta:
            for i, resultado in enumerate(generados):
                destino = Path(carpeta) / f"{i}.db"
                destino.write_bytes(resultado["data"])
                with closing(sqlite3.connect(destino)) as db:
                    self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    leche += db.execute("SELECT Cantidad, TipoMedida FROM Linea WHERE Codigo='504'").fetchall()
                    if db.execute("SELECT 1 FROM Linea WHERE Codigo='504'").fetchone():
                        otros = db.execute("SELECT Codigo, Cantidad, TipoMedida FROM Linea WHERE Codigo <> '504' ORDER BY Codigo").fetchall()
                        self.assertEqual(otros, [("276681", 1, "bulto"), ("292546", 1, "bulto")])
        self.assertEqual(leche, [(1, "semipale")])
        self.assertEqual(hashlib.sha256(PDF_LOCAL.read_bytes()).digest(), hashlib.sha256(original).digest())
