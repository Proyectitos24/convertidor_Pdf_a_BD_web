import tempfile
import unittest
from pathlib import Path

from services import am613a_converter as am
from tests import _am613a_sintetico as sx


class MixtosPolloTests(unittest.TestCase):
    def linea(self, b=2, uxb=6, s=1, total=13):
        return {"bultos": b, "uxb": uxb, "suel": s, "total": total}

    def test_mixtos_pollo_usan_total(self):
        self.assertEqual(am.cantidad_y_tipo(self.linea(), "pollo_carne"), (13, "unidad", None))
        self.assertEqual(am.cantidad_y_tipo(self.linea(2, 16, 12, 44), "pollo_carne"), (44, "unidad", None))

    def test_otros_grupos_siguen_rechazando_mixtos(self):
        for grupo in (None, "seco", "refrigerado", "congelado", "fruta_verdura", "almacen_central", "sin_clasificar"):
            with self.subTest(grupo=grupo):
                self.assertIsNotNone(am.cantidad_y_tipo(self.linea(), grupo)[2])

    def test_no_adivina_totales_o_uxb(self):
        for linea in (self.linea(total=12), self.linea(total=None), self.linea(uxb=None), self.linea(uxb=0)):
            self.assertIsNotNone(am.cantidad_y_tipo(linea, "pollo_carne")[2])

    def test_solo_bultos_y_solo_sueltas_no_cambian(self):
        self.assertEqual(am.cantidad_y_tipo(self.linea(1, 5, 0, 5), "pollo_carne"), (1, "bulto", None))
        self.assertEqual(am.cantidad_y_tipo(self.linea(0, 6, 3, 3), "pollo_carne"), (3, "unidad", None))

    def test_proceso_completo_propaga_grupo(self):
        pagina = sx.cabecera() + sx.particion(1) + sx.cabecera_columnas() + [sx.articulo("6423", "POLLO", 2, 6, 1, 13)] + sx.totales(2, 1, 13) + sx.pie()
        with tempfile.TemporaryDirectory() as ruta:
            pdf = Path(ruta) / "AM613A.pdf"
            sx.escribir_pdf(pdf, [pagina])
            resultado = am.process_pdf(pdf, Path(ruta) / "pollo", "pollo_carne")
            self.assertEqual((resultado["articulos"][0]["cantidad"], resultado["articulos"][0]["tipo"]), (13, "unidad"))
            with self.assertRaises(am.Am613aError):
                am.process_pdf(pdf, Path(ruta) / "seco", "seco")
