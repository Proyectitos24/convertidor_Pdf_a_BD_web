"""
Validación OPCIONAL con albaranes AM613A reales que no forman parte del
repositorio. Se busca en la carpeta AM613A_MUESTRAS_DIR (o, por defecto,
~/Downloads); si las muestras no existen, las pruebas se omiten sin
fallar. Los originales nunca se procesan: se copian a un
TemporaryDirectory y se comprueban sus checksums antes y después.
"""

import hashlib
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from services import am613a_converter, pdf_format

CARPETA = Path(os.environ.get("AM613A_MUESTRAS_DIR", Path.home() / "Downloads"))

# nombre del PDF -> (nombre .db, nº artículos, {código: (cantidad, tipo)},
# (suma Cantidad tipo "unidad", suma Cantidad tipo "bulto"))
# El material auxiliar (AUX/I-O) cuenta ahora como artículo, y su Codigo
# se guarda SIN el prefijo AUX/I-O (evidencia real, no inventada — ver
# services/am613a_converter.py). Ninguna de estas muestras produce una
# colisión entre un código numérico ya existente y uno de material
# auxiliar al quitarle el prefijo.
ESPERADO = {
    "AM613A____20261002092803248863.pdf": (
        "7-252345_2026-10-02.db", 128,
        {
            "283092": (4, "unidad"), "299121": (2, "bulto"), "224028": (3, "bulto"),
            "721413": (1, "unidad"), "721520": (2, "bulto"),
            "721858": (1, "bulto"), "721860": (1, "bulto"),
            "312634": (1, "bulto"), "312635": (1, "bulto"),
        },
        (17, 127),
    ),
    "AM613A____20261006093659247611.pdf": ("1-1567430_2026-10-06.db", 53, {}, (0, 62)),
    "AM613A____20261006093714771802.pdf": (
        "7-258095_2026-10-06.db", 83,
        {"530": (1, "bulto"), "304312": (1, "bulto")},
        (0, 89),
    ),
}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class MuestrasRealesAm613aTests(unittest.TestCase):
    def test_muestras_reales(self):
        disponibles = [n for n in ESPERADO if (CARPETA / n).is_file()]
        if not disponibles:
            self.skipTest(f"Sin muestras AM613A locales en {CARPETA}")

        antes = {n: _sha(CARPETA / n) for n in disponibles}
        with tempfile.TemporaryDirectory() as tmp:
            for n in disponibles:
                with self.subTest(pdf=n):
                    copia = Path(tmp) / "copias" / n
                    copia.parent.mkdir(exist_ok=True)
                    shutil.copy2(CARPETA / n, copia)
                    self.assertEqual(pdf_format.detectar_formato_pdf(copia), pdf_format.AM613A)

                    r = am613a_converter.process_pdf(copia, Path(tmp) / "salida" / n, "seco")
                    nombre, n_filas, ejemplos, (suma_unidad, suma_bulto) = ESPERADO[n]
                    self.assertEqual(Path(r["db_saved"]).name, nombre)
                    self.assertEqual(len(r["articulos"]), n_filas)

                    conn = sqlite3.connect(r["db_saved"])
                    try:
                        filas = conn.execute("SELECT Codigo, Cantidad, TipoMedida FROM Linea").fetchall()
                        self.assertEqual(len(filas), n_filas)
                        por_codigo = {c: (q, t) for c, q, t in filas}
                        for codigo, valor in ejemplos.items():
                            self.assertEqual(por_codigo[codigo], valor)
                        self.assertEqual(sum(q for _, q, t in filas if t == "unidad"), suma_unidad)
                        self.assertEqual(sum(q for _, q, t in filas if t == "bulto"), suma_bulto)
                    finally:
                        conn.close()

        self.assertEqual({n: _sha(CARPETA / n) for n in disponibles}, antes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
