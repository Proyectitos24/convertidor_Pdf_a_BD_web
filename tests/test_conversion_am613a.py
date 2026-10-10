"""
Integración de AM613A en services/conversion_service.py y en la
publicación sin duplicados (services/conversion_publication.py), con
PDFs AM613A sintéticos y Supabase/R2 sustituidos por FakeBackend.
Los PDFs RF626A/RF625A de regresión son copias de las muestras
versionadas de Tiendas/, nunca los originales.
"""

import hashlib
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import batch_convert
import cajas_azules
import services.conversion_publication as pub
from services import am613a_converter, conversion_service
from tests import _am613a_sintetico as sx
from tests.test_conversion_publication import FakeBackend
from tests.test_conversion_service import FakeUpload

REPO_ROOT = Path(__file__).resolve().parent.parent
CREATED = datetime(2026, 10, 2, 10, tzinfo=timezone.utc)
EXPIRES = datetime(2026, 10, 3, tzinfo=timezone.utc)


def _git_ls_files(pattern):
    out = subprocess.check_output(["git", "ls-files", pattern], cwd=str(REPO_ROOT), text=True)
    return sorted(REPO_ROOT / line for line in out.splitlines() if line.strip())


_MUESTRAS = _git_ls_files("Tiendas/**/pdfs/*.pdf") + _git_ls_files("Tiendas/**/db/*.db")
_CHECKSUMS_ANTES = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in _MUESTRAS}


def tearDownModule():
    despues = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in _MUESTRAS}
    assert despues == _CHECKSUMS_ANTES, "Una prueba modificó una muestra versionada"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        d = Path(cls._tmp.name)
        cls.am_a = sx.escribir_pdf(d / "a.pdf", sx.documento_basico()).read_bytes()
        cls.am_b = sx.escribir_pdf(d / "b.pdf", sx.documento_basico(albaran="1-1567430", fecha="6/10/26")).read_bytes()
        malo = sx.cabecera(albaran="7- 111111") + sx.particion(1) + sx.cabecera_columnas() + [
            sx.articulo("1", "AMBIGUO", 1, 6, 2, 8)
        ] + sx.totales(1, 2, 8)
        cls.am_malo = sx.escribir_pdf(d / "malo.pdf", [malo]).read_bytes()
        cls.desconocido = sx.escribir_pdf(d / "x.pdf", [["SIN IDENTIFICADOR DE FORMATO"]]).read_bytes()
        cls.contradictorio = sx.escribir_pdf(
            d / "c.pdf", [sx.cabecera(identificador="CPD-AM613A"), sx.cabecera(identificador="CPD-RF626A")]
        ).read_bytes()

        copias = d / "copias"
        copias.mkdir()
        cls.rf626a = cls.rf625a = None
        for original in _git_ls_files("Tiendas/**/pdfs/*.pdf"):
            copia = copias / original.name
            shutil.copy2(original, copia)
            formato = conversion_service.detectar_formato(copia)
            if formato == "RF626A" and cls.rf626a is None:
                cls.rf626a = copia.read_bytes()
            if formato == "RF625A" and cls.rf625a is None:
                cls.rf625a = copia.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class ConversionAm613aTests(_Base):
    def test_am613a_con_grupo_genera_un_db_con_ese_grupo(self):
        generados, resumen = conversion_service.convert_uploaded_files(
            [FakeUpload("cualquier_nombre.pdf", self.am_a)], {_sha(self.am_a): "congelado"}
        )
        self.assertEqual([g["name"] for g in generados], ["7-252345_2026-10-02.db"])
        self.assertEqual(resumen, [{"archivo": "cualquier_nombre.pdf", "estado": "ok", "detalle": "7-252345_2026-10-02.db"}])
        with tempfile.TemporaryDirectory() as t:
            ruta = Path(t) / "x.db"
            ruta.write_bytes(generados[0]["data"])
            conn = sqlite3.connect(ruta)
            meta = dict(conn.execute("SELECT Clave, Valor FROM Metadata"))
            conn.close()
        self.assertEqual((meta["grupo"], meta["grupo_fuente"], meta["formato_pdf"]), ("congelado", "MANUAL", "AM613A"))

    def test_am613a_sin_grupo_es_error_y_no_genera_nada(self):
        with mock.patch.object(am613a_converter, "process_pdf") as proc:
            generados, resumen = conversion_service.convert_uploaded_files([FakeUpload("a.pdf", self.am_a)], {})
        proc.assert_not_called()
        self.assertEqual(generados, [])
        self.assertEqual(resumen[0]["estado"], "error")
        self.assertIn("sin grupo", resumen[0]["detalle"])

    def test_grupo_vinculado_al_contenido_no_al_nombre(self):
        # La elección es del PDF A; el PDF B (otro contenido) no la hereda
        # aunque se llame igual.
        generados, resumen = conversion_service.convert_uploaded_files(
            [FakeUpload("igual.pdf", self.am_a), FakeUpload("igual.pdf", self.am_b)], {_sha(self.am_a): "seco"}
        )
        self.assertEqual([g["name"] for g in generados], ["7-252345_2026-10-02.db"])
        self.assertEqual([r["estado"] for r in resumen], ["ok", "error"])

    def test_am613a_repetido_se_procesa_una_vez(self):
        with mock.patch.object(am613a_converter, "process_pdf", wraps=am613a_converter.process_pdf) as proc:
            generados, resumen = conversion_service.convert_uploaded_files(
                [FakeUpload("a.pdf", self.am_a), FakeUpload("renombrado.pdf", self.am_a)], {_sha(self.am_a): "seco"}
            )
        self.assertEqual(proc.call_count, 1)
        self.assertEqual(len(generados), 1)
        self.assertEqual([r["estado"] for r in resumen], ["ok", "duplicado"])

    def test_am613a_ambiguo_no_detiene_los_demas(self):
        grupos = {_sha(self.am_malo): "seco", _sha(self.am_a): "seco"}
        generados, resumen = conversion_service.convert_uploaded_files(
            [FakeUpload("malo.pdf", self.am_malo), FakeUpload("a.pdf", self.am_a), FakeUpload("rf.pdf", self.rf626a)],
            grupos,
        )
        estados = {r["archivo"]: r["estado"] for r in resumen}
        self.assertEqual(estados, {"malo.pdf": "error", "a.pdf": "ok", "rf.pdf": "ok"})
        self.assertIn("a la vez", next(r["detalle"] for r in resumen if r["archivo"] == "malo.pdf"))
        nombres = [g["name"] for g in generados]
        self.assertIn("7-252345_2026-10-02.db", nombres)
        self.assertFalse(any(n.startswith("7-111111") for n in nombres))
        self.assertTrue(any(n.startswith("packinglist_") for n in nombres))

    def test_formato_desconocido_o_contradictorio_no_va_a_ningun_parser(self):
        with mock.patch.object(batch_convert, "process_pdf") as rf626, \
             mock.patch.object(cajas_azules, "process_pdf") as rf625, \
             mock.patch.object(am613a_converter, "process_pdf") as am613:
            generados, resumen = conversion_service.convert_uploaded_files(
                [FakeUpload("RF626A_x.pdf", self.desconocido), FakeUpload("c.pdf", self.contradictorio)]
            )
        rf626.assert_not_called()
        rf625.assert_not_called()
        am613.assert_not_called()
        self.assertEqual(generados, [])
        self.assertEqual([r["estado"] for r in resumen], ["error", "error"])
        self.assertIn("no reconocido", resumen[0]["detalle"])
        self.assertIn("contradictorios", resumen[1]["detalle"])

    def test_am613a_de_subidas_unicos_por_contenido(self):
        subidas = [
            FakeUpload("a.pdf", self.am_a), FakeUpload("a_copia.pdf", self.am_a),
            FakeUpload("b.pdf", self.am_b), FakeUpload("rf.pdf", self.rf626a), FakeUpload("x.pdf", self.desconocido),
        ]
        cache = {}
        self.assertEqual(
            conversion_service.am613a_de_subidas(subidas, cache),
            [(_sha(self.am_a), "a.pdf"), (_sha(self.am_b), "b.pdf")],
        )
        # Segunda llamada: usa la caché, no vuelve a abrir los PDF.
        with mock.patch.object(conversion_service, "detectar_formato_bytes") as det:
            conversion_service.am613a_de_subidas(subidas, cache)
        det.assert_not_called()


class RegresionRf626aRf625aTests(_Base):
    def test_rf626a_y_rf625a_siguen_su_parser_y_esquema(self):
        generados, resumen = conversion_service.convert_uploaded_files(
            [FakeUpload("uno.pdf", self.rf626a), FakeUpload("dos.pdf", self.rf625a)]
        )
        self.assertEqual([r["estado"] for r in resumen], ["ok", "ok"])
        for g in generados:
            with self.subTest(db=g["name"]):
                self.assertTrue(g["name"].startswith(("packinglist_", "cajas_azules_")))
                with tempfile.TemporaryDirectory() as t:
                    ruta = Path(t) / "x.db"
                    ruta.write_bytes(g["data"])
                    conn = sqlite3.connect(ruta)
                    columnas = [r[1] for r in conn.execute("PRAGMA table_info(Linea)")]
                    meta = dict(conn.execute("SELECT Clave, Valor FROM Metadata"))
                    conn.close()
                self.assertEqual(columnas, ["id", "Etiqueta", "Codigo", "Descripcion", "Cantidad", "Falta", "TipoMedida"])
                self.assertNotEqual(meta["grupo_fuente"], "MANUAL")
                self.assertIn(meta["formato_pdf"], ("RF626A", "RF625A"))

    def test_resultado_identico_al_parser_directo(self):
        generados, _ = conversion_service.convert_uploaded_files([FakeUpload("p.pdf", self.rf626a)])
        with tempfile.TemporaryDirectory() as t:
            pdf = Path(t) / "e" / "p.pdf"
            pdf.parent.mkdir()
            pdf.write_bytes(self.rf626a)
            _, _, _, dbs = batch_convert.process_pdf(pdf, Path(t) / "s")
            directos = {Path(d).name: Path(d).read_bytes() for d in dbs}
        self.assertEqual({g["name"]: g["data"] for g in generados}, directos)


class PublicacionAm613aTests(_Base):
    def setUp(self):
        self.be = FakeBackend().instalar(self)

    def _publicar(self, data, grupo, store=None):
        store = store or {"id": "tienda-a", "code": "14196"}
        generados, _ = conversion_service.convert_uploaded_files([FakeUpload("a.pdf", data)], {_sha(data): grupo})
        return pub.publicar_db_generados(store, generados, CREATED, EXPIRES)

    def test_repetir_el_mismo_am613a_no_crea_filas_ni_objetos(self):
        self.assertEqual([r["estado"] for r in self._publicar(self.am_a, "seco")], [pub.GUARDADO])
        self.assertEqual([r["estado"] for r in self._publicar(self.am_a, "seco")], [pub.YA_DISPONIBLE])
        self.assertEqual((len(self.be.rows), len(self.be.uploads)), (1, 1))
        self.assertEqual(self.be.rows[0]["grupo"], "seco")

    def test_mismo_nombre_con_grupo_distinto_es_conflicto(self):
        self._publicar(self.am_a, "seco")
        r = self._publicar(self.am_a, "congelado")
        self.assertEqual([x["estado"] for x in r], [pub.CONFLICTO])
        self.assertEqual((len(self.be.rows), len(self.be.uploads)), (1, 1))

    def test_aislamiento_por_tienda(self):
        self._publicar(self.am_a, "seco", {"id": "tienda-a", "code": "14196"})
        r = self._publicar(self.am_a, "congelado", {"id": "tienda-b", "code": "14140"})
        self.assertEqual([x["estado"] for x in r], [pub.GUARDADO])
        self.assertEqual(sorted(x["store_id"] for x in self.be.rows), ["tienda-a", "tienda-b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
