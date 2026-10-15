"""
Pruebas de services/pdf_format.py y services/am613a_converter.py con
PDFs AM613A sintéticos (tests/_am613a_sintetico.py) generados dentro de
TemporaryDirectory. No dependen de los PDFs reales de Downloads.
"""

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

import fitz

from services import am613a_converter as am
from services import pdf_format
from services.db_fingerprint import huella_logica_de_ruta
from services.grupo_packinglist import GRUPO_ORDEN, SCHEMA_VERSION
from tests import _am613a_sintetico as sx

CONSULTA_ANTIGUA = "SELECT Etiqueta, Codigo, Descripcion, Cantidad, Falta FROM Linea ORDER BY id"


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def pdf(self, paginas, nombre="doc.pdf"):
        return sx.escribir_pdf(self.dir / nombre, paginas)

    def extraer(self, paginas):
        doc = fitz.open(str(self.pdf(paginas)))
        try:
            return am.extraer_documento(doc)
        finally:
            doc.close()

    def assert_error(self, paginas, fragmento):
        with self.assertRaises(am.Am613aError) as ctx:
            self.extraer(paginas)
        self.assertTrue(
            any(fragmento in p for p in ctx.exception.problemas),
            f"{fragmento!r} no está en {ctx.exception.problemas}",
        )
        return ctx.exception

    def convertir(self, paginas, grupo="seco", nombre="doc.pdf"):
        ruta = self.pdf(paginas, nombre)
        return am.process_pdf(ruta, self.dir / "salida", grupo)


def _pagina_simple(lineas, totales, albaran="7- 252345", tienda="14196", fecha="2/10/26", pagina=1, part=1):
    return (
        sx.cabecera(albaran=albaran, tienda=tienda, fecha=fecha, pagina=pagina)
        + sx.particion(part) + sx.cabecera_columnas() + lineas + sx.totales(*totales) + sx.pie()
    )


# ---------------------------------------------------------------------
# Detección de formato
# ---------------------------------------------------------------------

class DeteccionFormatoTests(_Base):
    def test_am613a_renombrado_como_rf626a_se_detecta_por_cabecera(self):
        ruta = self.pdf(sx.documento_basico(), nombre="RF626A____20260101.pdf")
        self.assertEqual(pdf_format.detectar_formato_pdf(ruta), pdf_format.AM613A)

    def test_detecta_rf626a_y_rf625a_por_identificador(self):
        for ident, esperado in (("CPD-RF626A", "RF626A"), ("CPD-RF625A", "RF625A")):
            with self.subTest(ident=ident):
                ruta = self.pdf([sx.cabecera(identificador=ident)], nombre=f"{ident}.pdf")
                self.assertEqual(pdf_format.detectar_formato_pdf(ruta), esperado)

    def test_sin_identificador_es_formato_no_reconocido(self):
        ruta = self.pdf([["SISTEMA .: GETAFE", "ALBARAN DE ENTREGA sin identificador"]], nombre="AM613A_x.pdf")
        with self.assertRaisesRegex(pdf_format.FormatoPdfError, "no reconocido"):
            pdf_format.detectar_formato_pdf(ruta)

    def test_identificadores_contradictorios(self):
        paginas = [sx.cabecera(identificador="CPD-AM613A"), sx.cabecera(identificador="CPD-RF626A")]
        with self.assertRaisesRegex(pdf_format.FormatoPdfError, "contradictorios"):
            pdf_format.detectar_formato_pdf(self.pdf(paginas))

    def test_identificador_no_soportado(self):
        with self.assertRaisesRegex(pdf_format.FormatoPdfError, "no soportado"):
            pdf_format.detectar_formato_pdf(self.pdf([sx.cabecera(identificador="CPD-XX999X")]))

    def test_identificador_en_el_cuerpo_no_cuenta(self):
        # Un "CPD-RF626A" en una descripción (cuerpo de la página) no
        # cambia el formato del AM613A ni crea contradicción.
        paginas = sx.documento_basico()
        paginas[1] = paginas[1][:12] + [sx.articulo("1", "CPD-RF626A", 1, 1, "", 1)] + paginas[1][12:]
        self.assertEqual(pdf_format.detectar_formato_pdf(self.pdf(paginas)), pdf_format.AM613A)

        solo_cuerpo = [["", "", "", "", "", "", "", "", "", "", "texto CPD-RF626A en el cuerpo"]]
        with self.assertRaisesRegex(pdf_format.FormatoPdfError, "no reconocido"):
            pdf_format.detectar_formato_pdf(self.pdf(solo_cuerpo, nombre="cuerpo.pdf"))

    def test_bytes_que_no_son_pdf(self):
        with self.assertRaises(pdf_format.FormatoPdfError):
            pdf_format.detectar_formato_bytes(b"no es un pdf")


# ---------------------------------------------------------------------
# Cabecera
# ---------------------------------------------------------------------

class CabeceraTests(_Base):
    def test_tienda_fecha_y_albaran(self):
        d = self.extraer(sx.documento_basico())
        self.assertEqual((d["tienda"], d["fecha"], d["albaran"]), ("14196", "2026-10-02", "7-252345"))

    def test_normalizacion_del_albaran(self):
        self.assertEqual(am.normalizar_albaran("7- 252345"), "7-252345")
        self.assertEqual(am.normalizar_albaran("1-1567430"), "1-1567430")
        self.assertEqual(am.normalizar_albaran("  7 -  258095   NUMERO DE PEDIDO"), "7-258095")
        self.assertIsNone(am.normalizar_albaran("252345"))
        self.assertIsNone(am.normalizar_albaran(""))

    def test_nombre_db_con_albaran_y_fecha(self):
        d = self.extraer(sx.documento_basico(albaran="1-1567430", fecha="6/10/26"))
        self.assertEqual(d["nombre_db"], "1-1567430_2026-10-06.db")

    def test_falta_el_albaran(self):
        paginas = [[l for l in p if not l.startswith("NUMERO DE ALBARAN")] for p in sx.documento_basico()]
        self.assert_error(paginas, "No se encontró el número de albarán")

    def test_albaran_ilegible_no_se_completa(self):
        self.assert_error(sx.documento_basico(albaran="252345"), "albarán ilegible")

    def test_albaranes_contradictorios(self):
        paginas = sx.documento_basico()
        paginas[1] = sx.cabecera(albaran="7- 999999", pagina=2) + paginas[1][5:]
        self.assert_error(paginas, "número de albarán contradictorio")

    def test_tiendas_contradictorias(self):
        paginas = sx.documento_basico()
        paginas[2] = sx.cabecera(tienda="14140", pagina=3) + paginas[2][5:]
        self.assert_error(paginas, "la tienda contradictorio")

    def test_fecha_invalida(self):
        self.assert_error(sx.documento_basico(fecha="31/02/26"), "Fecha inválida")


# ---------------------------------------------------------------------
# Artículos
# ---------------------------------------------------------------------

class ArticulosTests(_Base):
    def setUp(self):
        super().setUp()
        self.d = self.extraer(sx.documento_basico())
        self.por_codigo = {a["codigo"]: a for a in self.d["articulos"]}

    def test_paginas_particiones_y_dos_bloques_en_una_pagina(self):
        self.assertEqual(
            [a["codigo"] for a in self.d["articulos"]],
            ["283092", "6649", "721413", "299121", "721520", "224028", "312634", "530", "48710"],
        )

    def test_unidades_sueltas_usan_total_unidades(self):
        self.assertEqual((self.por_codigo["283092"]["cantidad"], self.por_codigo["283092"]["tipo"]), (4, "unidad"))

    def test_bultos_usan_bultos_no_uxb_ni_total(self):
        self.assertEqual((self.por_codigo["299121"]["cantidad"], self.por_codigo["299121"]["tipo"]), (2, "bulto"))
        self.assertEqual((self.por_codigo["224028"]["cantidad"], self.por_codigo["224028"]["tipo"]), (3, "bulto"))

    def test_aux_e_io_se_incluyen_sin_el_prefijo_en_codigo(self):
        # El material auxiliar es un artículo más: mismas reglas de
        # Cantidad/TipoMedida, misma descripción. Al guardarlo se le
        # quita EXCLUSIVAMENTE el prefijo AUX/I-O de su Codigo.
        self.assertEqual((self.por_codigo["721413"]["cantidad"], self.por_codigo["721413"]["tipo"]), (1, "unidad"))
        self.assertEqual((self.por_codigo["721520"]["cantidad"], self.por_codigo["721520"]["tipo"]), (2, "bulto"))
        self.assertEqual((self.por_codigo["312634"]["cantidad"], self.por_codigo["312634"]["tipo"]), (1, "bulto"))
        codigos = set(self.por_codigo)
        self.assertFalse({"AUX721413", "AUX721520", "I/O312634"} & codigos)

    def test_incidencias_auditoria_restos_y_totales_excluidos(self):
        self.assertFalse({"303436", "145652", "222222"} & set(self.por_codigo))

    def test_descripcion_sin_precios(self):
        self.assertEqual(self.por_codigo["48710"]["descripcion"], "GINEBRA 37,5 BARMO 70 CL")


class NormalizacionCodigoAuxIoTests(_Base):
    def test_prefijo_aux_se_quita_conservando_ceros_iniciales(self):
        pag = _pagina_simple([sx.articulo("AUX007", "GUANTES", 1, 1, "", 1)], (1, 0, 1))
        d = self.extraer([pag])
        self.assertEqual(d["articulos"][0]["codigo"], "007")

    def test_prefijo_io_se_quita_conservando_ceros_iniciales(self):
        pag = _pagina_simple([sx.articulo("I/O007", "ARTICULO I/O", 1, 1, "", 1)], (1, 0, 1))
        d = self.extraer([pag])
        self.assertEqual(d["articulos"][0]["codigo"], "007")

    def test_codigo_ya_numerico_no_se_toca(self):
        pag = _pagina_simple([sx.articulo("721413", "PRODUCTO NORMAL", 1, 1, "", 1)], (1, 0, 1))
        d = self.extraer([pag])
        self.assertEqual(d["articulos"][0]["codigo"], "721413")

    def test_colision_de_codigo_tras_quitar_prefijo_rechaza_el_documento(self):
        # "AUX721413" y "721413" son DOS artículos distintos en el
        # documento original; si al quitar el prefijo acaban con el
        # mismo Codigo, no se fusionan silenciosamente: se rechaza el
        # documento entero para que alguien lo revise.
        pag = _pagina_simple([
            sx.articulo("721413", "PRODUCTO NORMAL", 1, 1, "", 1),
            sx.articulo("AUX721413", "GUANTE AUX", 1, 1, "", 1),
        ], (2, 0, 2))
        self.assert_error([pag], "coinciden en")

    def test_dos_prefijos_distintos_que_colisionan_tambien_se_rechazan(self):
        pag = _pagina_simple([
            sx.articulo("AUX007", "GUANTES", 1, 1, "", 1),
            sx.articulo("I/O007", "ARTICULO I/O", 1, 1, "", 1),
        ], (2, 0, 2))
        self.assert_error([pag], "coinciden en")


class CantidadesAmbiguasTests(_Base):
    def test_bultos_y_sueltas_a_la_vez(self):
        pag = _pagina_simple([sx.articulo("1", "A", 1, 6, 2, 8)], (1, 2, 8))
        self.assert_error([pag], "bultos y unidades sueltas a la vez")

    def test_sin_cantidades(self):
        pag = _pagina_simple([sx.articulo("1", "A", 0, 6, 0, 0)], (0, 0, 0))
        self.assert_error([pag], "sin bultos ni unidades")

    def test_cantidad_ilegible(self):
        pag = _pagina_simple([sx.articulo("1", "A", "1O", 6, "", 6)], (1, 0, 6))
        self.assert_error([pag], "valor ilegible en bultos")

    def test_total_incoherente_con_bultos_por_uxb(self):
        pag = _pagina_simple([sx.articulo("1", "A", 2, 6, "", 13)], (2, 0, 13))
        self.assert_error([pag], "no coincide con Bultos")

    def test_total_incoherente_con_sueltas(self):
        pag = _pagina_simple([sx.articulo("1", "A", 0, 6, 3, 4)], (0, 3, 4))
        self.assert_error([pag], "no coincide con Und. Suel.")

    def test_totales_de_particion_no_cuadran(self):
        pag = _pagina_simple([sx.articulo("1", "A", 2, 6, "", 12)], (3, 0, 12))
        self.assert_error([pag], "las líneas suman")

    def test_faltan_totales_de_la_particion(self):
        pag = sx.cabecera() + sx.particion(1) + sx.cabecera_columnas() + [sx.articulo("1", "A", 2, 6, "", 12)] + sx.pie()
        self.assert_error([pag], "no se encontraron sus totales")

    def test_codigo_no_numerico(self):
        pag = _pagina_simple([sx.articulo("X12", "A", 1, 6, "", 6)], (1, 0, 6))
        self.assert_error([pag], "código no numérico")

    def test_sin_articulos(self):
        pag = sx.cabecera() + sx.particion(1) + sx.cabecera_columnas() + sx.totales(0, 0, 0) + sx.pie()
        self.assert_error([pag], "ningún artículo")

    def test_documento_con_error_no_genera_db(self):
        pag = _pagina_simple([sx.articulo("1", "A", 1, 6, 2, 8), sx.articulo("2", "B", 1, 6, "", 6)], (2, 2, 14))
        with self.assertRaises(am.Am613aError):
            self.convertir([pag])
        self.assertEqual(list((self.dir / "salida").glob("*.db")) if (self.dir / "salida").exists() else [], [])


# ---------------------------------------------------------------------
# SQLite y Metadata
# ---------------------------------------------------------------------

class SqliteTests(_Base):
    def setUp(self):
        super().setUp()
        self.resultado = self.convertir(sx.documento_basico(), grupo="refrigerado")
        self.db = Path(self.resultado["db_saved"])
        self.conn = sqlite3.connect(self.db)

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def test_nombre_y_un_unico_db(self):
        self.assertEqual(self.db.name, "7-252345_2026-10-02.db")
        self.assertEqual(list(self.db.parent.glob("*.db")), [self.db])

    def test_consulta_antigua_sigue_funcionando(self):
        filas = self.conn.execute(CONSULTA_ANTIGUA).fetchall()
        self.assertEqual(filas[0], ("AM613A_14196_7-252345", "283092", "TABLET LIMPIAD PROTE BOTIK 30UDS", 4, 0))
        self.assertEqual(len(filas), 9)
        self.assertTrue(all(f[4] == 0 for f in filas))

    def test_etiqueta_sintetica(self):
        self.assertEqual(self.conn.execute("SELECT Etiqueta FROM Etiqueta").fetchall(), [("AM613A_14196_7-252345",)])
        self.assertEqual(am.etiqueta_sintetica("14196", "7-252345"), "AM613A_14196_7-252345")

    def test_tipo_de_medida_por_linea_en_columna_aditiva_al_final(self):
        columnas = [r[1] for r in self.conn.execute("PRAGMA table_info(Linea)")]
        self.assertEqual(columnas, ["id", "Etiqueta", "Codigo", "Descripcion", "Cantidad", "Falta", "TipoMedida"])
        tipos = dict(self.conn.execute("SELECT Codigo, TipoMedida FROM Linea"))
        self.assertEqual(tipos["283092"], "unidad")
        self.assertEqual(tipos["299121"], "bulto")

    def test_tipo_de_medida_restringido(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad, TipoMedida) VALUES ('e','c','d',1,'kilo')"
            )

    def test_metadata_cuatro_claves(self):
        meta = dict(self.conn.execute("SELECT Clave, Valor FROM Metadata"))
        self.assertEqual(meta, {
            "schema_version": SCHEMA_VERSION,
            "grupo": "refrigerado",
            "grupo_fuente": "MANUAL",
            "formato_pdf": "AM613A",
            "tienda": "14196",
        })
        self.assertEqual(SCHEMA_VERSION, "2")

    def test_tablas_codigo_y_descripcion(self):
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM Codigo").fetchone()[0], 9)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM Descripcion").fetchone()[0], 9)


class GrupoYEscrituraTests(_Base):
    def test_cada_grupo_valido_se_guarda_tal_cual(self):
        for i, grupo in enumerate(GRUPO_ORDEN):
            with self.subTest(grupo=grupo):
                r = am.process_pdf(self.pdf(sx.documento_basico(), f"{i}.pdf"), self.dir / f"s{i}", grupo)
                conn = sqlite3.connect(r["db_saved"])
                try:
                    self.assertEqual(conn.execute("SELECT Valor FROM Metadata WHERE Clave='grupo'").fetchone()[0], grupo)
                finally:
                    conn.close()

    def test_grupo_obligatorio_y_valido(self):
        for grupo in (None, "", "otro", "SECO"):
            with self.subTest(grupo=grupo):
                with self.assertRaisesRegex(am.Am613aError, "Grupo no válido"):
                    am.process_pdf(self.pdf(sx.documento_basico()), self.dir / "salida", grupo)

    def test_no_sobrescribe_un_db_existente(self):
        self.convertir(sx.documento_basico())
        with self.assertRaises(FileExistsError):
            self.convertir(sx.documento_basico(), nombre="otro.pdf")

    def test_no_modifica_ni_mueve_el_pdf(self):
        ruta = self.pdf(sx.documento_basico())
        antes = hashlib.sha256(ruta.read_bytes()).hexdigest()
        am.process_pdf(ruta, self.dir / "salida", "seco")
        self.assertTrue(ruta.exists())
        self.assertEqual(hashlib.sha256(ruta.read_bytes()).hexdigest(), antes)

    def test_mismo_codigo_mismo_tipo_se_suma_y_tipos_distintos_no(self):
        agrupados = am.agrupar_articulos([
            {"codigo": "1", "descripcion": "A", "cantidad": 2, "tipo": "bulto"},
            {"codigo": "1", "descripcion": "A", "cantidad": 3, "tipo": "unidad"},
            {"codigo": "1", "descripcion": "A", "cantidad": 1, "tipo": "bulto"},
        ])
        self.assertEqual(
            [(a["codigo"], a["tipo"], a["cantidad"]) for a in agrupados],
            [("1", "bulto", 3), ("1", "unidad", 3)],
        )


class HuellaAm613aTests(_Base):
    def _huella(self, articulos, grupo="seco", nombre="x.db"):
        ruta = self.dir / nombre
        am.write_db("AM613A_14196_7-1", articulos, ruta, grupo)
        return huella_logica_de_ruta(ruta)

    def test_huella_distingue_tipo_de_medida_y_grupo(self):
        base = [{"codigo": "1", "descripcion": "A", "cantidad": 4, "tipo": "bulto"}]
        otra_medida = [{"codigo": "1", "descripcion": "A", "cantidad": 4, "tipo": "unidad"}]
        h = self._huella(base, nombre="a.db")
        self.assertEqual(h, self._huella(base, nombre="b.db"))
        self.assertNotEqual(h, self._huella(otra_medida, nombre="c.db"))
        self.assertNotEqual(h, self._huella(base, grupo="congelado", nombre="d.db"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
