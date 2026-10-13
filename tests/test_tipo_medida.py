"""
Pruebas de TipoMedida en RF626A (batch_convert.py y convertir_pdf.py) y
RF625A (cajas_azules.py), del contrato compartido services/tipo_medida.py
y de su efecto en la huella lógica y en la publicación sin duplicados.

  - Muestras reales: los PDFs y .db versionados de Tiendas/ se descubren
    con `git ls-files`, se copian a un TemporaryDirectory y se procesan
    allí; nunca se procesa ni modifica un original (checksums antes y
    después del módulo). Los .db de referencia se abren en solo lectura.
  - Casos sin muestra real (Formato U en ambas columnas, desconocido,
    ausente, medidas mezcladas...): PDFs sintéticos (tests/_listados_sinteticos.py).
  - Supabase/R2: FakeBackend en memoria; las funciones reales de cliente
    se sustituyen por otras que fallan si alguien las llama.
"""

import hashlib
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import fitz

import batch_convert
import cajas_azules
import convertir_pdf
import services.conversion_publication as pub
from services import am613a_converter, conversion_service
from services import tipo_medida as tm
from services.db_fingerprint import huella_logica_de_bytes, huella_logica_de_ruta
from services.pdf_format import detectar_formato_pdf
from tests import _listados_sinteticos as ls
from tests.test_conversion_publication import FakeBackend
from tests.test_conversion_service import FakeUpload
from tests.test_grupo_packinglist import FILAS_EXTRA_CONOCIDAS_PREEXISTENTES, RESULTADO_ESPERADO

REPO_ROOT = Path(__file__).resolve().parent.parent
CONSULTA_ANTIGUA = "SELECT Etiqueta, Codigo, Descripcion, Cantidad, Falta FROM Linea ORDER BY id"


def _git_ls_files(pattern):
    out = subprocess.check_output(["git", "ls-files", pattern], cwd=str(REPO_ROOT), text=True)
    return sorted(REPO_ROOT / line for line in out.splitlines() if line.strip())


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


_PDFS = _git_ls_files("Tiendas/**/pdfs/*.pdf")
_DBS_REFERENCIA = {p.name: p for p in _git_ls_files("Tiendas/**/db/*.db")}
_MUESTRAS = _PDFS + list(_DBS_REFERENCIA.values())
_CHECKSUMS_ANTES = {str(p): _sha(p) for p in _MUESTRAS}


def tearDownModule():
    assert {str(p): _sha(p) for p in _MUESTRAS} == _CHECKSUMS_ANTES, "Una prueba modificó una muestra versionada"


def _conectar_ro(ruta):
    return sqlite3.connect(Path(ruta).resolve().as_uri() + "?mode=ro", uri=True)


def _leer(ruta):
    conn = _conectar_ro(ruta)
    try:
        columnas = [r[1] for r in conn.execute("PRAGMA table_info(Linea)")]
        antiguas = Counter(conn.execute(CONSULTA_ANTIGUA))
        con_tipo = (
            list(conn.execute("SELECT Codigo, Descripcion, Cantidad, TipoMedida FROM Linea ORDER BY id"))
            if "TipoMedida" in columnas else None
        )
        etiquetas = [r[0] for r in conn.execute("SELECT Etiqueta FROM Etiqueta")]
        tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        meta = dict(conn.execute("SELECT Clave, Valor FROM Metadata")) if "Metadata" in tablas else None
    finally:
        conn.close()
    return {"columnas": columnas, "antiguas": antiguas, "con_tipo": con_tipo, "etiquetas": etiquetas, "meta": meta}


def _totales_impresos(pdf, formato):
    """Totales de bultos/unidades impresos en el propio listado (evidencia del mapeo)."""
    patron = r"Total (unidades|bultos)[ \t]*\.+[ \t]*(\d[\d.]*)" if formato == "RF626A" \
        else r"TOTAL (UNIDADES|BULTOS)[ \t]*\.+[ \t]*(\d[\d.]*)"
    doc = fitz.open(str(pdf))
    try:
        texto = "\n".join(p.get_text("text") for p in doc)
    finally:
        doc.close()
    tot = Counter()
    for clave, valor in re.findall(patron, texto):
        tot[tm.TIPO_UNIDAD if clave.lower() == "unidades" else tm.TIPO_BULTO] += int(valor.replace(".", ""))
    return tot


# ---------------------------------------------------------------------
# Contrato compartido
# ---------------------------------------------------------------------

class ContratoTests(unittest.TestCase):
    def test_mapeo_exacto_b_y_u(self):
        self.assertEqual(tm.FORMATO_A_TIPO, {"B": "bulto", "U": "unidad", "S": "semipale", "P": "pale"})
        self.assertEqual(tm.tipo_desde_formato(["B"]), ("bulto", None))
        self.assertEqual(tm.tipo_desde_formato(["U"]), ("unidad", None))

    def test_sin_coincidencias_parciales_ni_aproximaciones(self):
        for valor in ("b", "u", " B", "B.", "BU", "UB", "Bulto", "UND", "J", "C", ""):
            with self.subTest(valor=valor):
                tipo, problema = tm.tipo_desde_formato([valor])
                self.assertIsNone(tipo)
                self.assertIn("sin equivalencia", problema)

    def test_ausente_y_contradictorio(self):
        self.assertEqual(tm.tipo_desde_formato([]), (None, "sin Formato"))
        tipo, problema = tm.tipo_desde_formato(["B", "U"])
        self.assertIsNone(tipo)
        self.assertIn("contradictorio", problema)

    def test_sobreimpresion_en_negrita_cuenta_una_vez(self):
        rango = (268.8, 302.4)
        lado = [(28.8, 57.6, "248783"), (283.2, 288.0, "B"), (283.2, 288.0, "B")]
        self.assertEqual(tm.tipo_de_linea(lado, rango), ("bulto", None))

    def test_formato_fuera_de_la_columna_es_ausente(self):
        self.assertEqual(tm.tipo_de_linea([(312.0, 316.8, "B")], (268.8, 302.4)), (None, "sin Formato"))
        self.assertEqual(tm.tipo_de_linea([(283.2, 288.0, "B")], None), (None, "sin Formato"))


class FormatoVacioUnidadTests(unittest.TestCase):
    RANGO_FORMATO = (268.8, 302.4)
    RANGO_CANTIDAD = (225.6, 264.0)

    def test_producto_con_formato_vacio_se_informa(self):
        lado = [(28.8, 57.6, "272536"), (62.4, 124.8, "YOGUR"), (259.2, 264.0, "3"), (345.6, 350.4, "3")]
        self.assertEqual(
            tm.problema_linea_no_reconocida(lado, self.RANGO_FORMATO, self.RANGO_CANTIDAD),
            "sin Formato: no puede determinarse su medida",
        )

    def test_totales_pies_y_cabeceras_no_son_productos(self):
        for lado in (
            [(48.0, 72.0, "Total"), (76.8, 105.6, "bultos"), (259.2, 264.0, "11")],
            [(230.4, 302.4, "TOTAL"), (259.2, 264.0, "16")],
            [(403.2, 451.2, "Impreso"), (259.2, 264.0, "1")],
            [(28.8, 52.8, "------"), (225.6, 264.0, "--------")],
        ):
            with self.subTest(lado=lado):
                self.assertIsNone(tm.problema_linea_no_reconocida(lado, self.RANGO_FORMATO, self.RANGO_CANTIDAD))

    def test_codigo_sin_cantidad_no_es_producto(self):
        # P.ej. una línea partida que solo repite el código: sin cantidad
        # no hay artículo que medir.
        lado = [(28.8, 57.6, "248783")]
        self.assertIsNone(tm.problema_linea_no_reconocida(lado, self.RANGO_FORMATO, self.RANGO_CANTIDAD))

    def test_sin_cabecera_de_formato_no_se_inventa_nada(self):
        lado = [(28.8, 57.6, "272536"), (259.2, 264.0, "3")]
        self.assertIsNone(tm.problema_linea_no_reconocida(lado, None, self.RANGO_CANTIDAD))


class EsquemaTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _nuevo(self):
        ruta = self.dir / "n.db"
        batch_convert.write_db("E1", {("1", "A", "bulto"): 2}, str(ruta), "seco", "AREA", "RF626A")
        return ruta

    def test_columna_aditiva_al_final_y_consulta_antigua(self):
        datos = _leer(self._nuevo())
        self.assertEqual(datos["columnas"], ["id", "Etiqueta", "Codigo", "Descripcion", "Cantidad", "Falta", "TipoMedida"])
        self.assertEqual(list(datos["antiguas"]), [("E1", "1", "A", 2, 0)])

    def test_tipo_medida_obligatorio_y_restringido(self):
        conn = sqlite3.connect(self._nuevo())
        try:
            for valor in (None, "", "kilo", "Bulto", "B"):
                with self.subTest(valor=valor), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad, TipoMedida) VALUES ('E','c','d',1,?)",
                        (valor,),
                    )
            conn.execute("INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad) VALUES ('E','c','d',1)") \
                if False else None
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad) VALUES ('E','c','d',1)")
        finally:
            conn.close()

    def test_write_db_rechaza_tipo_invalido_sin_crear_archivo(self):
        for modulo in (batch_convert, cajas_azules, convertir_pdf):
            with self.subTest(modulo=modulo.__name__):
                ruta = self.dir / f"{modulo.__name__}.db"
                with self.assertRaises(ValueError):
                    modulo.write_db("E1", {("1", "A", None): 2}, ruta, "seco", "AREA", "RF626A")
                self.assertFalse(ruta.exists())

    def _antiguo(self, nombre="antiguo.db"):
        ruta = self.dir / nombre
        conn = sqlite3.connect(ruta)
        conn.execute("CREATE TABLE Etiqueta (Etiqueta TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE Linea (id INTEGER PRIMARY KEY AUTOINCREMENT, Etiqueta TEXT NOT NULL, Codigo TEXT NOT NULL,"
            " Descripcion TEXT NOT NULL, Cantidad INTEGER NOT NULL, Falta INTEGER DEFAULT 0)"
        )
        conn.execute("INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad) VALUES ('E1','1','A',2)")
        conn.commit()
        conn.close()
        return ruta

    def test_destino_antiguo_no_se_modifica_ni_se_rellena(self):
        for modulo in (batch_convert, cajas_azules, convertir_pdf):
            with self.subTest(modulo=modulo.__name__):
                ruta = self._antiguo(f"{modulo.__name__}.db")
                antes = _sha(ruta)
                with self.assertRaises(tm.EsquemaIncompatibleError):
                    modulo.write_db("E1", {("1", "A", "bulto"): 5}, ruta, "seco", "AREA", "RF626A")
                self.assertEqual(_sha(ruta), antes)

    def test_ensure_schema_no_toca_un_linea_antiguo(self):
        ruta = self._antiguo()
        antes = _sha(ruta)
        conn = sqlite3.connect(ruta)
        try:
            with self.assertRaises(tm.EsquemaIncompatibleError):
                tm.ensure_schema(conn.cursor())
            tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        self.assertNotIn("Codigo", tablas)
        self.assertEqual(_sha(ruta), antes)

    def test_destino_con_el_contrato_actual_se_regenera(self):
        # Mismo comportamiento que antes para un .db ya con TipoMedida:
        # el escritor lo vacía y lo vuelve a escribir.
        ruta = self._nuevo()
        batch_convert.write_db("E1", {("1", "A", "unidad"): 7}, str(ruta), "seco", "AREA", "RF626A")
        self.assertEqual(_leer(ruta)["con_tipo"], [("1", "A", 7, "unidad")])

    def test_am613a_reutiliza_el_contrato_compartido(self):
        self.assertIs(am613a_converter.TIPO_BULTO, tm.TIPO_BULTO)
        self.assertIs(am613a_converter.TIPO_UNIDAD, tm.TIPO_UNIDAD)
        ruta = self.dir / "am.db"
        am613a_converter.write_db("AM613A_1_1-1", [{"codigo": "1", "descripcion": "A", "cantidad": 2, "tipo": "bulto"}], ruta, "seco")
        self.assertEqual(_leer(ruta)["columnas"][-1], "TipoMedida")


# ---------------------------------------------------------------------
# Muestras reales versionadas (copias)
# ---------------------------------------------------------------------

class MuestrasRealesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.resultados = []  # (pdf_original, formato, {nombre_db: ruta})
        for i, original in enumerate(_PDFS):
            copia = base / f"in{i}" / original.name
            copia.parent.mkdir()
            shutil.copy2(original, copia)
            formato = detectar_formato_pdf(copia)
            out = base / f"out{i}"
            if formato == "RF625A":
                r = cajas_azules.process_pdf(copia, out)
                dbs = [Path(r["db_saved"])]
            else:
                _, _, _, dbs = batch_convert.process_pdf(copia, out)
            cls.resultados.append((original, formato, {Path(d).name: Path(d) for d in dbs}))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_hay_rf626a_y_rf625a(self):
        formatos = Counter(f for _o, f, _d in self.resultados)
        self.assertGreater(formatos["RF626A"], 0)
        self.assertGreater(formatos["RF625A"], 0)

    def test_cada_fila_tiene_tipo_medida_valido(self):
        for original, _f, dbs in self.resultados:
            for nombre, ruta in dbs.items():
                with self.subTest(db=nombre):
                    filas = _leer(ruta)["con_tipo"]
                    self.assertTrue(filas)
                    self.assertTrue(all(t in tm.TIPOS_MEDIDA for *_x, t in filas))

    def test_cantidades_por_medida_cuadran_con_los_totales_impresos(self):
        # Evidencia del mapeo B=bulto / U=unidad en los propios documentos.
        for original, formato, dbs in self.resultados:
            with self.subTest(pdf=original.name):
                suma = Counter()
                for ruta in dbs.values():
                    for _c, _d, cantidad, tipo in _leer(ruta)["con_tipo"]:
                        suma[tipo] += cantidad
                self.assertEqual(+suma, +_totales_impresos(original, formato))

    def test_rf626a_real_con_formato_u_y_b(self):
        mixtos = [
            (o.name, n) for o, f, dbs in self.resultados if f == "RF626A"
            for n, r in dbs.items() if {t for *_x, t in _leer(r)["con_tipo"]} == {"bulto", "unidad"}
        ]
        self.assertTrue(mixtos, "Se esperaba al menos un RF626A real con líneas B y U")
        solo_u = [n for _o, f, dbs in self.resultados if f == "RF626A"
                  for n, r in dbs.items() if {t for *_x, t in _leer(r)["con_tipo"]} == {"unidad"}]
        self.assertTrue(solo_u)

    def test_rf625a_real_en_unidades(self):
        for _o, f, dbs in self.resultados:
            if f == "RF625A":
                for ruta in dbs.values():
                    self.assertEqual({t for *_x, t in _leer(ruta)["con_tipo"]}, {"unidad"})

    def test_columnas_antiguas_iguales_a_la_referencia(self):
        # Counter conserva la multiplicidad. Única diferencia admitida: la
        # fila preexistente documentada en test_grupo_packinglist (no la
        # introduce TipoMedida). En las muestras no hay ningún artículo
        # con dos medidas, así que separar por medida no cambia nada aquí.
        comparados = 0
        for _o, _f, dbs in self.resultados:
            for nombre, ruta in dbs.items():
                if nombre not in _DBS_REFERENCIA:
                    continue
                comparados += 1
                with self.subTest(db=nombre):
                    nuevo = _leer(ruta)
                    ref = _leer(_DBS_REFERENCIA[nombre])
                    etiqueta = nuevo["etiquetas"][0]
                    extra = Counter(FILAS_EXTRA_CONOCIDAS_PREEXISTENTES.get(etiqueta, set()))
                    self.assertEqual(nuevo["antiguas"], ref["antiguas"] + extra)
                    self.assertEqual(nuevo["etiquetas"], ref["etiquetas"])
        self.assertEqual(comparados, len(_DBS_REFERENCIA))

    def test_grupos_y_metadata_se_conservan(self):
        for _o, _f, dbs in self.resultados:
            for nombre, ruta in dbs.items():
                datos = _leer(ruta)
                etiqueta = datos["etiquetas"][0]
                if etiqueta not in RESULTADO_ESPERADO:
                    continue
                with self.subTest(db=nombre):
                    grupo, fuente, formato = RESULTADO_ESPERADO[etiqueta]
                    self.assertEqual(datos["meta"], {
                        "schema_version": "2", "grupo": grupo, "grupo_fuente": fuente, "formato_pdf": formato,
                    })

    def test_falta_sigue_siendo_cero(self):
        for _o, _f, dbs in self.resultados:
            for ruta in dbs.values():
                self.assertEqual({fila[4] for fila in _leer(ruta)["antiguas"]}, {0})


# ---------------------------------------------------------------------
# PDFs sintéticos
# ---------------------------------------------------------------------

class _Sintetico(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def rf626a(self, paginas, nombre="rf626a.pdf"):
        return ls.escribir_pdf(self.dir / nombre, paginas)

    def convertir_rf626a(self, paginas, modulo=batch_convert):
        pdf = self.rf626a(paginas)
        _, _, _, dbs = modulo.process_pdf(pdf, self.dir / "salida")
        return {Path(d).name: _leer(d) for d in dbs}


class Rf626aSinteticoTests(_Sintetico):
    def test_b_y_u_en_ambas_columnas_y_varias_paginas(self):
        p1 = ls.rf626a_pagina([
            ls.rf626a_fila(("135285", "YOGUR GRIEGO", 1, "B", 8), ("272536", "YOGUR NATURAL", 3, "U", 3)),
            ls.rf626a_fila(("13963", "LECHE ENTERA", 2, "U", 2), ("301952", "FLAN DE CAFE", 4, "B", 48)),
        ])
        p2 = ls.rf626a_pagina([ls.rf626a_fila(("308993", "POSTRE DUBAI", 5, "U", 5), ("310320", "LIMONADA", 6, "B", 36))], pagina=2)
        (datos,) = self.convertir_rf626a([p1, p2]).values()
        self.assertEqual(datos["con_tipo"], [
            ("135285", "YOGUR GRIEGO", 1, "bulto"),
            ("272536", "YOGUR NATURAL", 3, "unidad"),
            ("13963", "LECHE ENTERA", 2, "unidad"),
            ("301952", "FLAN DE CAFE", 4, "bulto"),
            ("308993", "POSTRE DUBAI", 5, "unidad"),
            ("310320", "LIMONADA", 6, "bulto"),
        ])

    def test_mismo_codigo_con_medidas_distintas_no_se_suma(self):
        p = ls.rf626a_pagina([
            ls.rf626a_fila(("135285", "YOGUR GRIEGO", 2, "B", 16), ("135285", "YOGUR GRIEGO", 3, "U", 3)),
            ls.rf626a_fila(("135285", "YOGUR GRIEGO", 1, "B", 8)),
        ])
        (datos,) = self.convertir_rf626a([p]).values()
        self.assertEqual(Counter(datos["con_tipo"]), Counter({
            ("135285", "YOGUR GRIEGO", 3, "bulto"): 1,
            ("135285", "YOGUR GRIEGO", 3, "unidad"): 1,
        }))
        # Excepción documentada respecto al comportamiento anterior: antes
        # se habría guardado una sola fila con Cantidad 6 (3 B + 3 U).
        self.assertEqual(sum(datos["antiguas"].values()), 2)

    def test_pagina_de_resumen_excluida(self):
        p = ls.rf626a_pagina([ls.rf626a_fila(("135285", "YOGUR GRIEGO", 1, "B", 8))])
        (datos,) = self.convertir_rf626a([p, ls.rf626a_resumen()]).values()
        self.assertEqual(datos["con_tipo"], [("135285", "YOGUR GRIEGO", 1, "bulto")])

    def _assert_documento_invalido(self, paginas, fragmento, modulo=batch_convert):
        pdf = self.rf626a(paginas)
        with self.assertRaises(tm.MedidaError) as ctx:
            modulo.process_pdf(pdf, self.dir / "salida")
        self.assertTrue(any(fragmento in p for p in ctx.exception.problemas), ctx.exception.problemas)
        self.assertEqual(list((self.dir / "salida").rglob("*.db")), [])
        self.assertTrue(pdf.exists(), "el PDF no debe moverse si el documento es inválido")
        return ctx.exception

    def test_formato_desconocido_informa_pagina_y_articulo(self):
        p1 = ls.rf626a_pagina([ls.rf626a_fila(("135285", "YOGUR GRIEGO", 1, "B", 8))])
        p2 = ls.rf626a_pagina([ls.rf626a_fila(None, ("13963", "LECHE ENTERA", 2, "J", 2))], pagina=2,
                              etiqueta="20022415822")
        e = self._assert_documento_invalido([p1, p2], "Página 2, código 13963: Formato 'J' sin equivalencia")
        self.assertEqual(len(e.problemas), 1)

    def test_presentacion_desconocida_no_se_convierte_en_una(self):
        p = ls.rf626a_pagina([ls.rf626a_fila(("999999", "ARTICULO NUEVO", 60, "S", 360))])
        self._assert_documento_invalido([p], "Presentación S/P sin equivalencia")

    def test_presentaciones_completas_conocidas_separan_s_y_p(self):
        import fitz
        for formato, medida in (("S", "semipale"), ("P", "pale")):
            p = ls.rf626a_pagina([ls.rf626a_fila(("504", "LECHE SEMIDESNATADA", 60, formato, 360))])
            ruta = self.dir / f"presentacion-{formato}.pdf"
            ls.escribir_pdf(ruta, [p])
            with fitz.open(ruta) as doc:
                for modulo in (batch_convert, convertir_pdf):
                    extractor = batch_convert.extract_items_from_page if modulo is batch_convert else convertir_pdf.extract_items_rf626a
                    items = extractor(doc[0])
                    self.assertIn(("504", "LECHE SEMIDESNATADA", 1, medida, None), items)

    def _pdf_con_formato_vacio(self, invertir=False):
        valido = ("135285", "YOGUR GRIEGO", 1, "B", 8)
        vacio = ("272536", "YOGUR NATURAL", 3, "", 3)
        izq, der = (vacio, valido) if invertir else (valido, vacio)
        return [ls.rf626a_pagina([ls.rf626a_fila(izq, der)])]

    def test_formato_realmente_vacio_rechaza_el_pdf(self):
        for modulo in (batch_convert, convertir_pdf):
            for invertir in (False, True):
                with self.subTest(modulo=modulo.__name__, vacio_a_la_izquierda=invertir):
                    paginas = self._pdf_con_formato_vacio(invertir)
                    # El Formato está vacío de verdad: ninguna palabra en su columna.
                    pdf = self.rf626a(paginas, nombre=f"comprobacion_{modulo.__name__}_{invertir}.pdf")
                    doc = fitz.open(str(pdf))
                    try:
                        palabras = doc[0].get_text("words")
                        rangos = tm.columnas_formato(palabras, doc[0].rect.width / 2)
                        en_formato = [w[4] for w in palabras if w[1] > tm.y_cabecera_formato(palabras)
                                      and any(x0 - 2.4 <= (w[0] + w[2]) / 2 <= x1 + 2.4 for x0, x1 in rangos.values())
                                      and set(w[4]) != {"-"}]
                    finally:
                        doc.close()
                    self.assertEqual(en_formato, ["B"])

                    salida = self.dir / f"salida_{modulo.__name__}_{invertir}"
                    pdf = self.rf626a(paginas, nombre=f"{modulo.__name__}_{invertir}.pdf")
                    with self.assertRaises(tm.MedidaError) as ctx:
                        modulo.process_pdf(pdf, salida)
                    self.assertEqual(
                        ctx.exception.problemas,
                        ["Página 1, código 272536: sin Formato: no puede determinarse su medida"],
                    )
                    self.assertEqual(list(salida.rglob("*.db")) if salida.exists() else [], [])
                    self.assertTrue(pdf.exists(), "el PDF no debe moverse si el documento es inválido")

    def test_totales_con_numero_bajo_cantidad_no_se_confunden_con_productos(self):
        filas = [ls.rf626a_fila(("135285", "YOGUR GRIEGO", 1, "B", 8))]
        pie = [
            ls._linea([(10, "Total"), (16, "bultos"), (23, "......"), (54, "1")]),
            ls._linea([(10, "TOTAL"), (16, "UNIDADES"), (25, "......"), (53, "16")]),
        ]
        p = ls.rf626a_cabecera() + ls.rf626a_columnas() + filas + pie + ls.rf626a_totales()
        for modulo in (batch_convert, convertir_pdf):
            with self.subTest(modulo=modulo.__name__):
                pdf = self.rf626a([p], nombre=f"pie_{modulo.__name__}.pdf")
                _, _, _, dbs = modulo.process_pdf(pdf, self.dir / f"salida_pie_{modulo.__name__}")
                self.assertEqual([_leer(d)["con_tipo"] for d in dbs], [[("135285", "YOGUR GRIEGO", 1, "bulto")]])

    def test_formato_ausente_de_su_columna(self):
        p = ls.rf626a_pagina([ls.rf626a_fila(("135285", "YOGUR GRIEGO", 1, "B", 8, 65))])
        self._assert_documento_invalido([p], "Página 1, código 135285: sin Formato")

    def test_pagina_sin_cabecera_de_formato(self):
        p = ls.rf626a_cabecera() + [ls.rf626a_fila(("135285", "YOGUR GRIEGO", 1, "B", 8))]
        self._assert_documento_invalido([p], "sin Formato")

    def test_un_error_en_una_etiqueta_impide_todas_las_del_documento(self):
        p1 = ls.rf626a_pagina([ls.rf626a_fila(("1111", "VALIDO", 1, "B", 8))], etiqueta="20022415821")
        p2 = ls.rf626a_pagina([ls.rf626a_fila(("2222", "INVALIDO", 1, "J", 8))], etiqueta="20022415822", pagina=2)
        self._assert_documento_invalido([p1, p2], "código 2222")

    def test_convertir_pdf_tambien_guarda_tipo_medida(self):
        p = ls.rf626a_pagina([ls.rf626a_fila(("135285", "YOGUR GRIEGO", 1, "B", 8), ("272536", "YOGUR NATURAL", 3, "U", 3))])
        (datos,) = self.convertir_rf626a([p], modulo=convertir_pdf).values()
        self.assertEqual(datos["con_tipo"], [("135285", "YOGUR GRIEGO", 1, "bulto"), ("272536", "YOGUR NATURAL", 3, "unidad")])

    def test_convertir_pdf_rechaza_formato_desconocido(self):
        p = ls.rf626a_pagina([ls.rf626a_fila(("135285", "YOGUR GRIEGO", 1, "J", 8))])
        self._assert_documento_invalido([p], "Formato 'J'", modulo=convertir_pdf)

    def test_destino_antiguo_no_mueve_el_pdf_ni_modifica_el_db(self):
        for modulo in (batch_convert, convertir_pdf):
            with self.subTest(modulo=modulo.__name__):
                salida = self.dir / f"salida_{modulo.__name__}"
                destino = salida / "Tienda_14196" / "2026-10-06" / "db" / "packinglist_14196_2026-10-06_etq_20022415821.db"
                destino.parent.mkdir(parents=True)
                conn = sqlite3.connect(destino)
                conn.execute("CREATE TABLE Linea (id INTEGER PRIMARY KEY AUTOINCREMENT, Etiqueta TEXT, Codigo TEXT,"
                             " Descripcion TEXT, Cantidad INTEGER, Falta INTEGER DEFAULT 0)")
                conn.commit()
                conn.close()
                antes = _sha(destino)
                pdf = self.rf626a([ls.rf626a_pagina([ls.rf626a_fila(("11", "A", 1, "B", 1))])], nombre=f"{modulo.__name__}.pdf")
                with self.assertRaises(tm.EsquemaIncompatibleError):
                    modulo.process_pdf(pdf, salida)
                self.assertEqual(_sha(destino), antes)
                self.assertTrue(pdf.exists())


class Rf625aSinteticoTests(_Sintetico):
    def _convertir(self, filas):
        pdf = ls.escribir_pdf(self.dir / "rf625a.pdf", [ls.rf625a_pagina(filas)])
        r = cajas_azules.process_pdf(pdf, self.dir / "salida")
        return _leer(r["db_saved"])

    def test_u_y_b_en_ambas_columnas(self):
        datos = self._convertir([
            ls.rf625a_fila(("297243", "QUITAESMALT", 1, "U"), ("284105", "BAND DEP FACIAL", 2, "B")),
        ])
        self.assertEqual(datos["con_tipo"], [("297243", "QUITAESMALT", 1, "unidad"), ("284105", "BAND DEP FACIAL", 2, "bulto")])

    def test_mismo_codigo_con_medidas_distintas_no_se_suma(self):
        datos = self._convertir([ls.rf625a_fila(("297243", "QUITAESMALT", 1, "U"), ("297243", "QUITAESMALT", 2, "B"))])
        self.assertEqual(Counter((c, t) for c, _d, _q, t in datos["con_tipo"]), Counter({("297243", "unidad"): 1, ("297243", "bulto"): 1}))

    def test_formato_realmente_vacio_rechaza_el_pdf(self):
        for invertir in (False, True):
            with self.subTest(vacio_a_la_izquierda=invertir):
                valido = ("297243", "QUITAESMALT", 1, "U")
                vacio = ("284105", "BAND DEP FACIAL", 2, "")
                izq, der = (vacio, valido) if invertir else (valido, vacio)
                pdf = ls.escribir_pdf(self.dir / f"rf625a_vacio_{invertir}.pdf", [ls.rf625a_pagina([ls.rf625a_fila(izq, der)])])
                salida = self.dir / f"salida_vacio_{invertir}"
                with self.assertRaises(tm.MedidaError) as ctx:
                    cajas_azules.process_pdf(pdf, salida)
                self.assertEqual(
                    ctx.exception.problemas,
                    ["Página 1, código 284105: sin Formato: no puede determinarse su medida"],
                )
                self.assertEqual(list(salida.rglob("*.db")) if salida.exists() else [], [])
                self.assertTrue(pdf.exists())

    def test_formato_vacio_en_el_lote_no_detiene_otros_pdfs(self):
        malo = ls.escribir_pdf(self.dir / "malo.pdf", [ls.rf625a_pagina([
            ls.rf625a_fila(("297243", "QUITAESMALT", 1, "U"), ("284105", "BAND DEP FACIAL", 2, "")),
        ])]).read_bytes()
        bueno = ls.escribir_pdf(self.dir / "bueno.pdf", [ls.rf626a_pagina([
            ls.rf626a_fila(("135285", "YOGUR GRIEGO", 1, "B", 8)),
        ])]).read_bytes()
        generados, resumen = conversion_service.convert_uploaded_files([FakeUpload("malo.pdf", malo), FakeUpload("bueno.pdf", bueno)])
        self.assertEqual({r["archivo"]: r["estado"] for r in resumen}, {"malo.pdf": "error", "bueno.pdf": "ok"})
        self.assertIn("no puede determinarse su medida", next(r["detalle"] for r in resumen if r["archivo"] == "malo.pdf"))
        self.assertEqual([g["name"].startswith("packinglist_") for g in generados], [True])

    def test_formato_ausente_impide_el_documento(self):
        pdf = ls.escribir_pdf(self.dir / "rf625a.pdf", [ls.rf625a_pagina([ls.rf625a_fila(("297243", "QUITAESMALT", 1, "U", 79))])])
        with self.assertRaises(tm.MedidaError) as ctx:
            cajas_azules.process_pdf(pdf, self.dir / "salida")
        self.assertIn("Página 1, código 297243: sin Formato", ctx.exception.problemas[0])
        self.assertEqual(list((self.dir / "salida").rglob("*.db")), [])
        self.assertTrue(pdf.exists())


    def test_formato_que_el_parser_no_admite_no_se_descarta_en_silencio(self):
        pdf = ls.escribir_pdf(self.dir / "rf625a.pdf", [ls.rf625a_pagina([
            ls.rf625a_fila(("297243", "QUITAESMALT", 1, "U"), ("284105", "BAND DEP FACIAL", 2, "C")),
        ])])
        with self.assertRaises(tm.MedidaError) as ctx:
            cajas_azules.process_pdf(pdf, self.dir / "salida")
        self.assertEqual(ctx.exception.problemas, ["Página 1, código 284105: línea no reconocida con Formato 'C'"])
        self.assertEqual(list((self.dir / "salida").rglob("*.db")), [])


class LoteTests(_Sintetico):
    def test_documento_invalido_no_detiene_los_demas_del_lote(self):
        malo = self.rf626a([ls.rf626a_pagina([ls.rf626a_fila(("11", "A", 1, "J", 1))])], "malo.pdf").read_bytes()
        bueno = self.rf626a([ls.rf626a_pagina([ls.rf626a_fila(("22", "B2", 1, "B", 1))], etiqueta="20022415999")], "bueno.pdf").read_bytes()
        rf625 = ls.escribir_pdf(self.dir / "c.pdf", [ls.rf625a_pagina([ls.rf625a_fila(("297243", "QUITA", 1, "U"))])]).read_bytes()
        generados, resumen = conversion_service.convert_uploaded_files(
            [FakeUpload("malo.pdf", malo), FakeUpload("bueno.pdf", bueno), FakeUpload("c.pdf", rf625)]
        )
        self.assertEqual({r["archivo"]: r["estado"] for r in resumen}, {"malo.pdf": "error", "bueno.pdf": "ok", "c.pdf": "ok"})
        self.assertIn("Formato 'J'", next(r["detalle"] for r in resumen if r["archivo"] == "malo.pdf"))
        self.assertEqual(len(generados), 2)


# ---------------------------------------------------------------------
# Huella lógica y duplicados
# ---------------------------------------------------------------------

class HuellaYDuplicadosTests(_Sintetico):
    CREATED = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)
    EXPIRES = datetime(2026, 10, 7, tzinfo=timezone.utc)
    STORE = {"id": "tienda-a", "code": "14196"}

    def _db(self, nombre, acc):
        ruta = self.dir / nombre
        batch_convert.write_db("E1", acc, str(ruta), "seco", "AREA", "RF626A")
        return ruta

    def test_misma_cantidad_distinta_medida_distinta_huella(self):
        a = huella_logica_de_ruta(self._db("a.db", {("1", "A", "bulto"): 4}))
        b = huella_logica_de_ruta(self._db("b.db", {("1", "A", "unidad"): 4}))
        self.assertNotEqual(a, b)

    def _antiguo_equivalente(self):
        # Mismas filas y Metadata que un .db nuevo, pero con el contrato
        # anterior (Linea sin TipoMedida).
        ruta = self.dir / "antiguo.db"
        conn = sqlite3.connect(ruta)
        cur = conn.cursor()
        for t in ("Etiqueta", "Codigo", "Descripcion"):
            cur.execute(f"CREATE TABLE {t} ({t} TEXT PRIMARY KEY)")
        cur.execute("CREATE TABLE Linea (id INTEGER PRIMARY KEY AUTOINCREMENT, Etiqueta TEXT NOT NULL, Codigo TEXT NOT NULL,"
                    " Descripcion TEXT NOT NULL, Cantidad INTEGER NOT NULL, Falta INTEGER DEFAULT 0)")
        cur.execute("INSERT INTO Etiqueta VALUES ('20022415821')")
        cur.execute("INSERT INTO Codigo VALUES ('135285')")
        cur.execute("INSERT INTO Descripcion VALUES ('YOGUR GRIEGO')")
        cur.execute("INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad, Falta) VALUES ('20022415821','135285','YOGUR GRIEGO',1,0)")
        from services import grupo_packinglist as grp
        grp.write_metadata(cur, "refrigerado", "Area=REFRIGERADO", "RF626A")
        conn.commit()
        conn.close()
        return ruta

    def _generados(self):
        pdf = self.rf626a([ls.rf626a_pagina([ls.rf626a_fila(("135285", "YOGUR GRIEGO", 1, "B", 8))])]).read_bytes()
        generados, _ = conversion_service.convert_uploaded_files([FakeUpload("a.pdf", pdf)])
        return generados

    def test_db_antiguo_y_nuevo_con_mismas_filas_no_son_identicos(self):
        (nuevo,) = self._generados()
        antiguo = self._antiguo_equivalente()
        datos_antiguo = _leer(antiguo)
        with tempfile.TemporaryDirectory() as t:
            ruta_nueva = Path(t) / "n.db"
            ruta_nueva.write_bytes(nuevo["data"])
            datos_nuevo = _leer(ruta_nueva)
        self.assertEqual(datos_nuevo["antiguas"], datos_antiguo["antiguas"])
        self.assertEqual(datos_nuevo["meta"], datos_antiguo["meta"])
        self.assertNotEqual(nuevo["fingerprint"], huella_logica_de_ruta(antiguo))

    def test_repetir_la_conversion_no_publica_duplicados(self):
        be = FakeBackend().instalar(self)
        r1 = pub.publicar_db_generados(self.STORE, self._generados(), self.CREATED, self.EXPIRES)
        r2 = pub.publicar_db_generados(self.STORE, self._generados(), self.CREATED, self.EXPIRES)
        self.assertEqual([r["estado"] for r in r1 + r2], [pub.GUARDADO, pub.YA_DISPONIBLE])
        self.assertEqual((len(be.rows), len(be.uploads)), (1, 1))

    def test_reconvertir_un_nombre_disponible_publicado_con_el_esquema_antiguo_es_conflicto(self):
        # Comportamiento documentado: un .db antiguo disponible (sin huella
        # o con huella del contrato anterior) no se sobrescribe ni se da
        # por idéntico; la nueva versión con TipoMedida queda en conflicto.
        be = FakeBackend().instalar(self)
        generados = self._generados()
        antiguo = self._antiguo_equivalente().read_bytes()
        for huella in (None, huella_logica_de_bytes(antiguo)):
            with self.subTest(huella_guardada=huella):
                be.rows.clear()
                be.uploads.clear()
                fila = be.fila("tienda-a", generados[0]["name"], antiguo, fingerprint=huella)
                (r,) = pub.publicar_db_generados(self.STORE, generados, self.CREATED, self.EXPIRES)
                self.assertEqual(r["estado"], pub.CONFLICTO)
                self.assertEqual(be.uploads, [])
                self.assertEqual(be.r2[fila["object_key"]], antiguo)

    def test_cero_conexiones_reales(self):
        FakeBackend().instalar(self)
        prohibido = mock.Mock(side_effect=AssertionError("conexión real prohibida en pruebas"))
        with mock.patch("services.supabase_service.get_admin_client", prohibido), \
             mock.patch("services.store_db.get_admin_client", prohibido), \
             mock.patch("services.r2_service.get_r2_client", prohibido):
            resultados = pub.publicar_db_generados(self.STORE, self._generados(), self.CREATED, self.EXPIRES)
        self.assertEqual([r["estado"] for r in resultados], [pub.GUARDADO])
        prohibido.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
