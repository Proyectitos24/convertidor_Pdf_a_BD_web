"""
Pruebas de services/conversion_service.convert_uploaded_files: PDFs
repetidos (por SHA-256 del contenido), PDFs distintos con el mismo
nombre y .db duplicados/conflictivos dentro de una misma operación.

Nunca se ejecuta process_pdf sobre los PDFs versionados de Tiendas/:
se copian a un TemporaryDirectory y solo se leen las copias. Los
checksums de todas las muestras se comprueban antes y después del
módulo.
"""

import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import batch_convert
import cajas_azules
from services import conversion_service
from services.db_fingerprint import huella_logica_de_bytes

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_ls_files(pattern):
    out = subprocess.check_output(["git", "ls-files", pattern], cwd=str(REPO_ROOT), text=True)
    return sorted(REPO_ROOT / line for line in out.splitlines() if line.strip())


def _checksums(paths):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


_MUESTRAS = _git_ls_files("Tiendas/**/pdfs/*.pdf") + _git_ls_files("Tiendas/**/db/*.db")
_CHECKSUMS_ANTES = _checksums(_MUESTRAS)


def tearDownModule():
    assert _checksums(_MUESTRAS) == _CHECKSUMS_ANTES, "Una prueba modificó una muestra versionada"


class FakeUpload:
    """Doble mínimo de UploadedFile de Streamlit (name + getvalue)."""

    def __init__(self, name, data):
        self.name = name
        self._data = data

    def getvalue(self):
        return self._data


class _ConCopias(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        copias = Path(cls._tmp.name) / "copias"
        copias.mkdir()
        cls.packing = []
        cls.rf625a = None
        for original in _git_ls_files("Tiendas/**/pdfs/*.pdf"):
            copia = copias / f"{len(cls.packing)}_{original.name}"
            shutil.copy2(original, copia)
            datos = copia.read_bytes()
            if conversion_service.detectar_tipo_pdf(copia) == "rf625a":
                cls.rf625a = datos
            else:
                cls.packing.append(datos)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class MuestrasDisponiblesTests(_ConCopias):
    def test_hay_al_menos_dos_packing_y_un_rf625a(self):
        self.assertGreaterEqual(len(self.packing), 2)
        self.assertIsNotNone(self.rf625a)
        self.assertNotEqual(self.packing[0], self.packing[1])


class PdfsRepetidosTests(_ConCopias):
    def test_mismo_pdf_mismo_nombre_se_procesa_una_vez(self):
        datos = self.packing[0]
        with mock.patch.object(batch_convert, "process_pdf", wraps=batch_convert.process_pdf) as spy:
            generados, resumen = conversion_service.convert_uploaded_files(
                [FakeUpload("a.pdf", datos), FakeUpload("a.pdf", datos)]
            )
        self.assertEqual(spy.call_count, 1)
        nombres = [g["name"] for g in generados]
        self.assertEqual(len(nombres), len(set(nombres)))
        duplicados = [r for r in resumen if r["estado"] == "duplicado"]
        self.assertEqual(len(duplicados), 1)
        self.assertEqual(duplicados[0]["archivo"], "a.pdf")
        self.assertIn("repetido", duplicados[0]["detalle"])

    def test_mismo_pdf_con_nombre_distinto_se_procesa_una_vez(self):
        datos = self.rf625a
        with mock.patch.object(cajas_azules, "process_pdf", wraps=cajas_azules.process_pdf) as spy:
            generados, resumen = conversion_service.convert_uploaded_files(
                [FakeUpload("uno.pdf", datos), FakeUpload("otro.pdf", datos)]
            )
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(len(generados), 1)
        self.assertEqual(generados[0]["source_pdf"], "uno.pdf")
        duplicado = [r for r in resumen if r["estado"] == "duplicado"]
        self.assertEqual(len(duplicado), 1)
        self.assertEqual(duplicado[0]["archivo"], "otro.pdf")
        self.assertIn("uno.pdf", duplicado[0]["detalle"])

    def test_pdfs_distintos_con_el_mismo_nombre_no_se_pisan(self):
        a, b = self.packing[0], self.packing[1]
        solo_a, _ = conversion_service.convert_uploaded_files([FakeUpload("x.pdf", a)])
        solo_b, _ = conversion_service.convert_uploaded_files([FakeUpload("x.pdf", b)])

        juntos, resumen = conversion_service.convert_uploaded_files(
            [FakeUpload("albaran.pdf", a), FakeUpload("albaran.pdf", b)]
        )

        self.assertTrue(solo_a and solo_b)
        self.assertFalse({g["name"] for g in solo_a} & {g["name"] for g in solo_b})
        esperado = {g["name"]: g["fingerprint"] for g in solo_a + solo_b}
        self.assertEqual({g["name"]: g["fingerprint"] for g in juntos}, esperado)
        self.assertEqual([r["estado"] for r in resumen], ["ok", "ok"])

    def test_bytes_entregados_identicos_a_process_pdf_directo(self):
        datos = self.packing[0]
        generados, _ = conversion_service.convert_uploaded_files([FakeUpload("p.pdf", datos)])

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "entrada" / "p.pdf"
            pdf.parent.mkdir()
            pdf.write_bytes(datos)
            _, _, _, dbs = batch_convert.process_pdf(pdf, Path(tmp) / "salida")
            directos = {Path(d).name: Path(d).read_bytes() for d in dbs}

        self.assertEqual({g["name"]: g["data"] for g in generados}, directos)

    def test_cada_db_lleva_su_huella_logica(self):
        generados, _ = conversion_service.convert_uploaded_files([FakeUpload("c.pdf", self.rf625a)])
        for g in generados:
            self.assertEqual(g["fingerprint"], huella_logica_de_bytes(g["data"]))


def _escribir_db(ruta, cantidad):
    batch_convert.write_db("E1", {("100", "LECHE"): cantidad}, str(ruta), "seco", "AREA", "RF626A")


NOMBRE_X = "packinglist_14196_2026-01-15_etq_1.db"
NOMBRE_Y = "packinglist_14196_2026-01-15_etq_2.db"
NOMBRE_Z = "packinglist_14196_2026-01-15_etq_3.db"


def _convertir_simulado(entradas):
    """
    entradas: [(nombre_pdf, {nombre_db: cantidad, ...}), ...]. process_pdf
    se simula escribiendo cada .db con el escritor real en el out_root
    de esa entrada; el contenido del PDF falso solo sirve para que sus
    SHA-256 sean distintos.
    """
    por_contenido = {}
    uploads = []
    for i, (nombre_pdf, dbs) in enumerate(entradas):
        contenido = f"%PDF-{i}".encode()
        por_contenido[contenido] = dbs
        uploads.append(FakeUpload(nombre_pdf, contenido))

    def falso_process_pdf(pdf_path, out_root):
        dbs = por_contenido[Path(pdf_path).read_bytes()]
        rutas = []
        for nombre_db, cantidad in dbs.items():
            db = Path(out_root) / "db" / nombre_db
            db.parent.mkdir(parents=True, exist_ok=True)
            _escribir_db(db, cantidad)
            rutas.append(db)
        return "14196", "2026-01-15", pdf_path, rutas

    with mock.patch.object(conversion_service, "detectar_tipo_pdf", return_value="packing"), \
         mock.patch.object(batch_convert, "process_pdf", side_effect=falso_process_pdf):
        return conversion_service.convert_uploaded_files(uploads)


class DbsDuplicadosEnLaOperacionTests(unittest.TestCase):
    """Varios PDFs distintos que generan el mismo nombre de .db en un lote."""

    def _conflicto(self, resumen, nombre=NOMBRE_X):
        conflictos = [r for r in resumen if r["estado"] == "conflicto"]
        self.assertEqual(len(conflictos), 1)
        self.assertEqual(conflictos[0]["db_file_name"], nombre)
        return conflictos[0]

    def test_mismo_nombre_y_mismo_contenido_logico_se_conserva_uno(self):
        generados, resumen = _convertir_simulado([("a.pdf", {NOMBRE_X: 5}), ("b.pdf", {NOMBRE_X: 5})])
        self.assertEqual(len(generados), 1)
        self.assertEqual(generados[0]["source_pdf"], "a.pdf")
        self.assertEqual([(r["archivo"], r["estado"]) for r in resumen], [("a.pdf", "ok"), ("b.pdf", "duplicado")])
        self.assertIn("a.pdf (n.º 1)", resumen[1]["detalle"])

    def test_mismo_nombre_con_datos_distintos_no_entrega_ninguna_version(self):
        generados, resumen = _convertir_simulado([("a.pdf", {NOMBRE_X: 5}), ("b.pdf", {NOMBRE_X: 6})])
        self.assertEqual(generados, [])
        conflicto = self._conflicto(resumen)
        self.assertEqual(conflicto["pdfs"], ["a.pdf (n.º 1)", "b.pdf (n.º 2)"])
        self.assertIn("ninguna versión", conflicto["detalle"])
        # La primera versión no aparece como convertida/entregada.
        self.assertEqual([r for r in resumen if r["estado"] == "ok"], [])
        self.assertNotIn(NOMBRE_X, " ".join(r["detalle"] for r in resumen if r["estado"] != "conflicto"))

    def test_tres_apariciones_la_tercera_igual_a_la_primera_no_se_reanade(self):
        generados, resumen = _convertir_simulado(
            [("a.pdf", {NOMBRE_X: 5}), ("b.pdf", {NOMBRE_X: 6}), ("c.pdf", {NOMBRE_X: 5})]
        )
        self.assertEqual(generados, [])
        conflicto = self._conflicto(resumen)
        self.assertEqual(conflicto["pdfs"], ["a.pdf (n.º 1)", "b.pdf (n.º 2)", "c.pdf (n.º 3)"])
        self.assertEqual([r["estado"] for r in resumen], ["conflicto"])

    def test_tres_apariciones_la_tercera_igual_a_la_segunda_no_se_reanade(self):
        generados, resumen = _convertir_simulado(
            [("a.pdf", {NOMBRE_X: 5}), ("b.pdf", {NOMBRE_X: 6}), ("c.pdf", {NOMBRE_X: 6})]
        )
        self.assertEqual(generados, [])
        self.assertEqual(len(self._conflicto(resumen)["pdfs"]), 3)

    def test_distinto_orden_de_entrada_mismo_resultado(self):
        for cantidades in ((5, 5, 6), (5, 6, 5), (6, 5, 5)):
            with self.subTest(cantidades=cantidades):
                entradas = [(f"p{i}.pdf", {NOMBRE_X: c}) for i, c in enumerate(cantidades)]
                generados, resumen = _convertir_simulado(entradas)
                self.assertEqual(generados, [])
                self.assertEqual(
                    self._conflicto(resumen)["pdfs"],
                    [f"p{i}.pdf (n.º {i + 1})" for i in range(3)],
                )
                self.assertFalse(any(r["estado"] in ("ok", "duplicado") for r in resumen))

    def test_otros_archivos_validos_del_lote_se_conservan(self):
        generados, resumen = _convertir_simulado(
            [
                ("a.pdf", {NOMBRE_X: 5, NOMBRE_Y: 1}),
                ("b.pdf", {NOMBRE_X: 6, NOMBRE_Z: 2}),
                ("c.pdf", {NOMBRE_Y: 1}),
            ]
        )
        self.assertEqual([(g["name"], g["source_pdf"]) for g in generados], [(NOMBRE_Y, "a.pdf"), (NOMBRE_Z, "b.pdf")])
        self.assertEqual(
            [(r["archivo"], r["estado"], r["detalle"]) for r in resumen if r["estado"] == "ok"],
            [("a.pdf", "ok", NOMBRE_Y), ("b.pdf", "ok", NOMBRE_Z)],
        )
        duplicados = [r for r in resumen if r["estado"] == "duplicado"]
        self.assertEqual([r["archivo"] for r in duplicados], ["c.pdf"])
        self.assertEqual(self._conflicto(resumen)["pdfs"], ["a.pdf (n.º 1)", "b.pdf (n.º 2)"])

    def test_pdfs_con_el_mismo_nombre_se_distinguen_por_posicion(self):
        _, resumen = _convertir_simulado([("x.pdf", {NOMBRE_X: 5}), ("x.pdf", {NOMBRE_X: 6})])
        self.assertEqual(self._conflicto(resumen)["pdfs"], ["x.pdf (n.º 1)", "x.pdf (n.º 2)"])

    def test_conflicto_no_se_sube_ni_se_publica(self):
        from datetime import datetime, timezone

        import services.conversion_publication as pub
        from tests.test_conversion_publication import FakeBackend

        backend = FakeBackend().instalar(self)
        publicar = mock.Mock(wraps=pub.publicar_convertido_reservado)
        reservar = mock.Mock(wraps=pub.reservar_publicacion)
        generados, resumen = _convertir_simulado(
            [("a.pdf", {NOMBRE_X: 5, NOMBRE_Y: 1}), ("b.pdf", {NOMBRE_X: 6}), ("c.pdf", {NOMBRE_X: 5})]
        )
        with mock.patch.object(pub, "publicar_convertido_reservado", publicar), \
             mock.patch.object(pub, "reservar_publicacion", reservar):
            resultados = pub.publicar_db_generados(
                {"id": "tienda-a", "code": "14196"}, generados,
                datetime(2026, 1, 15, tzinfo=timezone.utc), datetime(2026, 1, 16, tzinfo=timezone.utc),
            )

        self.assertEqual([r["archivo"] for r in resultados], [NOMBRE_Y])
        self.assertTrue(all(not k.endswith(NOMBRE_X) for k in backend.uploads))
        self.assertEqual(len(backend.uploads), 1)
        self.assertNotIn(NOMBRE_X, [c.kwargs["db_file_name"] for c in publicar.call_args_list])
        self.assertNotIn(NOMBRE_X, [c.args[1] for c in reservar.call_args_list])
        self.assertEqual([r["db_file_name"] for r in backend.rows], [NOMBRE_Y])

        resultado = pub.construir_resultado(resumen, resultados)
        self.assertEqual(resultado["conteo"]["conflictos"], 1)
        self.assertEqual(resultado["conteo"]["guardados"], 1)
        self.assertNotEqual(resultado["nivel"], "success")

    def test_flujo_de_la_pestana_no_envia_el_nombre_conflictivo(self):
        import ui.convert_tab as convert_tab

        enviados = []

        def publicar(store, generated_files, created_at, expires_at):
            enviados.extend(g["name"] for g in generated_files)
            return []

        entradas = [("a.pdf", {NOMBRE_X: 5}), ("b.pdf", {NOMBRE_X: 6, NOMBRE_Z: 2})]
        with mock.patch.object(convert_tab, "convert_uploaded_files", side_effect=lambda _u: _convertir_simulado(entradas)), \
             mock.patch.object(convert_tab, "publicar_db_generados", side_effect=publicar):
            resultado = convert_tab._ejecutar_operacion({"id": "tienda-a", "code": "14196"}, [object()])

        self.assertEqual(enviados, [NOMBRE_Z])
        self.assertEqual(resultado["conteo"]["conflictos"], 1)
        self.assertEqual(resultado["nivel"], "warning")

    def test_db_ilegible_marca_el_pdf_como_error_sin_registrar_nada(self):
        def falso_process_pdf(pdf_path, out_root):
            db = Path(out_root) / "roto.db"
            db.write_bytes(b"no es sqlite" * 50)
            return "1", "2026-01-01", pdf_path, [db]

        with mock.patch.object(conversion_service, "detectar_tipo_pdf", return_value="packing"), \
             mock.patch.object(batch_convert, "process_pdf", side_effect=falso_process_pdf):
            generados, resumen = conversion_service.convert_uploaded_files([FakeUpload("a.pdf", b"%PDF-x")])

        self.assertEqual(generados, [])
        self.assertEqual(resumen[0]["estado"], "error")


class DirectoriosSeparadosTests(unittest.TestCase):
    def test_cada_entrada_usa_su_propio_directorio(self):
        rutas = []

        def falso_process_pdf(pdf_path, out_root):
            rutas.append((Path(pdf_path), Path(out_root), Path(pdf_path).read_bytes()))
            return "1", "2026-01-01", pdf_path, []

        with mock.patch.object(conversion_service, "detectar_tipo_pdf", return_value="packing"), \
             mock.patch.object(batch_convert, "process_pdf", side_effect=falso_process_pdf):
            conversion_service.convert_uploaded_files(
                [FakeUpload("x.pdf", b"%PDF-1"), FakeUpload("x.pdf", b"%PDF-2")]
            )

        (pdf1, out1, d1), (pdf2, out2, d2) = rutas
        self.assertNotEqual(pdf1, pdf2)
        self.assertNotEqual(out1, out2)
        self.assertEqual((d1, d2), (b"%PDF-1", b"%PDF-2"))
        self.assertEqual(pdf1.name, "x.pdf")



class RepetirConversionDeExtremoAExtremoTests(_ConCopias):
    """Convertir dos veces el mismo PDF y publicar: ni otra fila ni otro objeto."""

    def test_segunda_operacion_no_crea_filas_ni_objetos(self):
        from datetime import datetime, timezone

        import services.conversion_publication as pub
        from tests.test_conversion_publication import FakeBackend

        backend = FakeBackend().instalar(self)
        store = {"id": "tienda-a", "code": "14196"}
        ahora = datetime(2026, 1, 15, tzinfo=timezone.utc)
        manana = datetime(2026, 1, 16, tzinfo=timezone.utc)

        primera, _ = conversion_service.convert_uploaded_files([FakeUpload("a.pdf", self.packing[0])])
        r1 = pub.publicar_db_generados(store, primera, ahora, manana)
        filas, subidas = len(backend.rows), len(backend.uploads)

        segunda, _ = conversion_service.convert_uploaded_files([FakeUpload("renombrado.pdf", self.packing[0])])
        r2 = pub.publicar_db_generados(store, segunda, ahora, manana)

        self.assertTrue(primera)
        self.assertEqual({r["estado"] for r in r1}, {pub.GUARDADO})
        self.assertEqual({r["estado"] for r in r2}, {pub.YA_DISPONIBLE})
        self.assertEqual((len(backend.rows), len(backend.uploads)), (filas, subidas))
        self.assertEqual(filas, len(primera))

if __name__ == "__main__":
    unittest.main(verbosity=2)
