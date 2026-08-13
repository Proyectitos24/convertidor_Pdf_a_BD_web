"""
Pruebas del modulo services.grupo_packinglist y de su integracion en
batch_convert.py / cajas_azules.py / convertir_pdf.py.

Solo biblioteca estandar (unittest). Ninguna prueba escribe, mueve ni
regenera los PDFs/.db versionados bajo Tiendas/: cada PDF real usado en
las pruebas de integracion se copia primero a un tempfile.TemporaryDirectory().
"""

import hashlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from urllib.request import pathname2url

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import batch_convert  # noqa: E402
import cajas_azules  # noqa: E402
from services import conversion_service  # noqa: E402
from services import grupo_packinglist as grp  # noqa: E402


def _git_ls_files(pattern):
    out = subprocess.check_output(
        ["git", "ls-files", pattern], cwd=str(REPO_ROOT), text=True
    )
    return sorted(REPO_ROOT / line for line in out.splitlines() if line.strip())


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _checksums(paths):
    return {str(p.relative_to(REPO_ROOT)): _sha256(p) for p in paths}


def _read_db_ro(path: Path):
    uri = f"file:{pathname2url(str(path))}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _metadata_dict(cur):
    cur.execute("SELECT Clave, Valor FROM Metadata")
    return dict(cur.fetchall())


def _linea_rows(cur):
    cur.execute(
        "SELECT Etiqueta, Codigo, Descripcion, Cantidad, Falta FROM Linea ORDER BY id"
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Proteccion de las muestras versionadas: checksums antes/despues de TODO
# el modulo de pruebas. Si cualquier prueba tocara un PDF/.db real, esto
# lo detectaria.
# ---------------------------------------------------------------------------

_MUESTRAS = _git_ls_files("Tiendas/**/pdfs/*.pdf") + _git_ls_files("Tiendas/**/db/*.db")
_CHECKSUMS_ANTES = _checksums(_MUESTRAS)


def tearDownModule():
    checksums_despues = _checksums(_MUESTRAS)
    if checksums_despues != _CHECKSUMS_ANTES:
        cambiados = [
            ruta
            for ruta in _CHECKSUMS_ANTES
            if _CHECKSUMS_ANTES[ruta] != checksums_despues.get(ruta)
        ]
        raise AssertionError(
            "Las muestras versionadas cambiaron durante las pruebas: " + ", ".join(cambiados)
        )


class ChecksumMuestrasTests(unittest.TestCase):
    def test_hay_muestras_descubiertas_por_git(self):
        pdfs = [p for p in _MUESTRAS if p.suffix == ".pdf"]
        dbs = [p for p in _MUESTRAS if p.suffix == ".db"]
        self.assertGreater(len(pdfs), 0)
        self.assertGreater(len(dbs), 0)

    def test_checksums_estables_durante_la_ejecucion(self):
        self.assertEqual(_checksums(_MUESTRAS), _CHECKSUMS_ANTES)


# ---------------------------------------------------------------------------
# Normalizacion y mapeo Area -> grupo
# ---------------------------------------------------------------------------

class NormalizarAreaTests(unittest.TestCase):
    def test_recorta_espacios_y_pasa_a_mayusculas(self):
        self.assertEqual(grp.normalizar_area("  refrigerado  "), "REFRIGERADO")

    def test_colapsa_espacios_internos_repetidos(self):
        self.assertEqual(grp.normalizar_area("SECO   ILLESCAS"), "SECO ILLESCAS")

    def test_valor_vacio_o_none(self):
        self.assertEqual(grp.normalizar_area(""), "")
        self.assertEqual(grp.normalizar_area(None), "")
        self.assertEqual(grp.normalizar_area("   "), "")


class AreaAGrupoTests(unittest.TestCase):
    def test_mapeo_confirmado(self):
        casos = {
            "SECO ILLESCAS": "almacen_central",
            "UNITARIO ILLESCAS": "almacen_central",
            "FLUJO TENSO": "pollo_carne",
            "FRUTA": "fruta_verdura",
            "SEMIPALETS": "seco",
            "SECO": "seco",
            "REFRIGERADO": "refrigerado",
            "CONGELADO": "congelado",
        }
        for area, grupo_esperado in casos.items():
            with self.subTest(area=area):
                self.assertEqual(grp.area_a_grupo(area), grupo_esperado)

    def test_seco_a_secas_mapea_a_seco_igual_que_semipalets(self):
        # Evidencia real: un mismo albaran de seco trae particiones/
        # etiquetas distintas, unas con Area=SEMIPALETS y otras con
        # Area=SECO. Ambas resuelven al mismo grupo canonico "seco".
        self.assertEqual(grp.area_a_grupo("SECO"), "seco")
        self.assertEqual(grp.area_a_grupo("SEMIPALETS"), "seco")
        self.assertEqual(grp.area_a_grupo("SECO"), grp.area_a_grupo("SEMIPALETS"))

    def test_seco_no_se_confunde_con_seco_illescas(self):
        # "SECO" y "SECO ILLESCAS" son valores de Area distintos que
        # resuelven a grupos distintos: seco vs almacen_central. Que
        # ahora "SECO" tenga grupo propio no debe hacer que colapse con
        # "SECO ILLESCAS" via coincidencia parcial.
        self.assertNotEqual(grp.area_a_grupo("SECO"), grp.area_a_grupo("SECO ILLESCAS"))
        self.assertEqual(grp.area_a_grupo("SECO ILLESCAS"), "almacen_central")

    def test_valor_desconocido(self):
        self.assertEqual(grp.area_a_grupo("ZONA NUEVA"), grp.SIN_CLASIFICAR)

    def test_valor_vacio(self):
        self.assertEqual(grp.area_a_grupo(""), grp.SIN_CLASIFICAR)
        self.assertEqual(grp.area_a_grupo(None), grp.SIN_CLASIFICAR)

    def test_sin_coincidencias_parciales(self):
        # "SECO" es subcadena de "SECO ILLESCAS" pero NO debe producir
        # el mismo grupo: la comparacion es de igualdad exacta.
        self.assertNotEqual(grp.area_a_grupo("SECO"), grp.area_a_grupo("SECO ILLESCAS"))
        self.assertEqual(grp.area_a_grupo("SECO ILLESCAS EXTRA"), grp.SIN_CLASIFICAR)
        self.assertEqual(grp.area_a_grupo("SEMIPALETS EXTRA"), grp.SIN_CLASIFICAR)
        self.assertEqual(grp.area_a_grupo("PRE FRUTA"), grp.SIN_CLASIFICAR)


class ResolverGrupoTests(unittest.TestCase):
    def test_area_ausente(self):
        self.assertEqual(grp.resolver_grupo([]), (grp.SIN_CLASIFICAR, "NO_DETECTADO"))

    def test_area_unica_valida(self):
        self.assertEqual(
            grp.resolver_grupo(["REFRIGERADO"]), ("refrigerado", "Area=REFRIGERADO")
        )

    def test_misma_area_repetida_se_conserva(self):
        self.assertEqual(
            grp.resolver_grupo(["CONGELADO", "CONGELADO", "CONGELADO"]),
            ("congelado", "Area=CONGELADO"),
        )

    def test_areas_contradictorias_grupos_distintos(self):
        # REFRIGERADO y CONGELADO resuelven a grupos canonicos distintos:
        # eso si es un conflicto real. grupo_fuente en orden alfabetico
        # (determinista, independiente del orden de aparicion en el PDF).
        grupo, fuente = grp.resolver_grupo(["REFRIGERADO", "CONGELADO"])
        self.assertEqual(grupo, grp.SIN_CLASIFICAR)
        self.assertEqual(fuente, "CONFLICTO:CONGELADO|REFRIGERADO")

    def test_area_desconocida_via_resolver(self):
        self.assertEqual(
            grp.resolver_grupo(["ZONA NUEVA"]), (grp.SIN_CLASIFICAR, "Area=ZONA NUEVA")
        )

    def test_seco_via_resolver(self):
        self.assertEqual(grp.resolver_grupo(["SECO"]), ("seco", "Area=SECO"))

    def test_semipalets_y_seco_mismo_grupo_no_es_conflicto(self):
        # Evidencia real: SEMIPALETS y SECO en particiones/etiquetas
        # distintas del mismo albaran. Como ambas resuelven al mismo
        # grupo canonico "seco", NO debe tratarse como conflicto.
        esperado = ("seco", "Area=SECO|SEMIPALETS")
        self.assertEqual(grp.resolver_grupo(["SEMIPALETS", "SECO"]), esperado)
        # El orden de aparicion no debe alterar el resultado (grupo_fuente
        # se ordena alfabeticamente de forma determinista).
        self.assertEqual(grp.resolver_grupo(["SECO", "SEMIPALETS"]), esperado)
        # Repeticiones no cambian nada tampoco.
        self.assertEqual(
            grp.resolver_grupo(["SECO", "SEMIPALETS", "SECO", "SEMIPALETS"]), esperado
        )

    def test_dos_areas_desconocidas_distintas_son_conflicto(self):
        # Que ambas caigan en el fallback sin_clasificar NO significa
        # que sean "del mismo grupo": podrian ser dos zonas nuevas y
        # distintas aun sin mapear. Esa combinacion no se puede resolver
        # con seguridad, asi que se marca como CONFLICTO.
        grupo, fuente = grp.resolver_grupo(["ZONA A", "ZONA B"])
        self.assertEqual(grupo, grp.SIN_CLASIFICAR)
        self.assertEqual(fuente, "CONFLICTO:ZONA A|ZONA B")

    def test_area_conocida_mas_desconocida_es_conflicto(self):
        # Una conocida (REFRIGERADO) y otra desconocida (ZONA NUEVA) no
        # deben colapsar a "refrigerado": no hay forma segura de saber
        # si ZONA NUEVA es tambien refrigerado o es otra cosa distinta.
        grupo, fuente = grp.resolver_grupo(["REFRIGERADO", "ZONA NUEVA"])
        self.assertEqual(grupo, grp.SIN_CLASIFICAR)
        self.assertEqual(fuente, "CONFLICTO:REFRIGERADO|ZONA NUEVA")


class MismaEtiquetaSemipaletsYSecoEnPaginasDistintasTests(unittest.TestCase):
    """
    Reproduce, con texto sintetico en el mismo formato de cabecera que
    los PDFs reales, el caso reportado por negocio: dentro de un mismo
    albaran de seco, una misma etiqueta aparece en una particion con
    Area=SEMIPALETS y en otra con Area=SECO. Ejercita el mismo camino
    que usa batch_convert.process_pdf(): extraer_etiqueta_real() +
    extraer_area_pagina() por pagina, acumular por etiqueta y resolver.
    """

    def test_misma_etiqueta_semipalets_y_seco_resuelve_a_seco_sin_conflicto(self):
        etiqueta = "20099999999"

        pagina_semipalets = (
            "NUMERO TOTAL DE CONTENEDORES :        2\n"
            "Area ...........:  SEMIPALETS                                    PARTICION  ....:       1\n"
            f"CONTENEDOR  ....:  H0                                           ETIQUETA   ....:   {etiqueta}\n"
        )
        pagina_seco = (
            "NUMERO TOTAL DE CONTENEDORES :        2\n"
            "Area ...........:  SECO                                         PARTICION  ....:       2\n"
            f"CONTENEDOR  ....:  H1                                           ETIQUETA   ....:   {etiqueta}\n"
        )

        areas_por_etiqueta = {}
        for page_text in (pagina_semipalets, pagina_seco):
            etq_real = grp.extraer_etiqueta_real(page_text)
            self.assertEqual(etq_real, etiqueta)
            area = grp.extraer_area_pagina(page_text)
            areas_por_etiqueta.setdefault(etq_real, []).append(area)

        self.assertEqual(areas_por_etiqueta[etiqueta], ["SEMIPALETS", "SECO"])

        grupo, grupo_fuente = grp.resolver_grupo(areas_por_etiqueta[etiqueta])
        self.assertEqual(grupo, "seco")
        self.assertEqual(grupo_fuente, "Area=SECO|SEMIPALETS")


# ---------------------------------------------------------------------------
# Extraccion anclada a la linea "Area" (nunca busqueda de palabra suelta)
# ---------------------------------------------------------------------------

class ExtraerAreaPaginaTests(unittest.TestCase):
    def test_linea_real_rf626a_con_particion(self):
        texto = (
            "NUMERO TOTAL DE CONTENEDORES :        1\n"
            "Area ...........:  REFRIGERADO                                  PARTICION  ....:       1\n"
            "CONTENEDOR  ....:  H0                                           ETIQUETA   ....:   20021563094\n"
        )
        self.assertEqual(grp.extraer_area_pagina(texto), "REFRIGERADO")

    def test_linea_real_rf625a_sin_particion(self):
        texto = (
            "NUMERO TOTAL DE CAJAS ......:     1\n"
            "Area ...........:  UNITARIO ILLESCAS\n"
            "Caja de plastico:  000000000000001498\n"
        )
        self.assertEqual(grp.extraer_area_pagina(texto), "UNITARIO ILLESCAS")

    def test_semipalets_sintetico_sin_muestra_real(self):
        # No existe ningun PDF real con Area=SEMIPALETS en el repositorio;
        # se construye una cabecera sintetica con el mismo formato que las
        # muestras RF626A reales.
        texto = (
            "NUMERO TOTAL DE CONTENEDORES :        1\n"
            "Area ...........:  SEMIPALETS                                    PARTICION  ....:       1\n"
            "CONTENEDOR  ....:  X0                                           ETIQUETA   ....:   99999999999\n"
        )
        area = grp.extraer_area_pagina(texto)
        self.assertEqual(area, "SEMIPALETS")
        self.assertEqual(grp.area_a_grupo(area), "seco")

    def test_no_busca_palabras_clave_sueltas_en_el_cuerpo(self):
        # Evidencia real: descripciones de producto que contienen "FRUTA"
        # en paginas cuya Area real es otra. Sin linea "Area", debe
        # devolver None, nunca "FRUTA".
        texto = (
            "     209794 BÍFIDUS FRUTAS 0%0%  BIFID 8X125         1"
            "                                                        "
            "297363 BM QUARK PROTEÍNAS   DIALA 200 G         1"
            "                                                        "
            "252356 GELATINA SABOR FRUTA DIALA 4X100         1    B            6\n"
        )
        self.assertIsNone(grp.extraer_area_pagina(texto))

    def test_area_real_no_se_confunde_con_producto_que_menciona_otra_area(self):
        texto = (
            "Area ...........:  REFRIGERADO                                  PARTICION  ....:       1\n"
            "CONTENEDOR  ....:  J0                                           ETIQUETA   ....:   20021574913\n"
            "      209794 BÍFIDUS FRUTAS 0%0%  BIFID 8X125         1\n"
        )
        self.assertEqual(grp.extraer_area_pagina(texto), "REFRIGERADO")

    def test_pagina_de_cierre_sin_area(self):
        texto = (
            "NUMERO DE ALBARAN ..........: 1-2005503\n"
            "NUMERO TOTAL DE CONTENEDORES :        1\n"
            "                                                                              --------------------\n"
            "          ALBARAN ORIGEN     ETIQUETA ORIGEN    ALBARAN DESTINO    ETIQUETA DESTINO    TIPO REMONTE\n"
            "                                                                                       * * * FIN LISTADO * * *\n"
        )
        self.assertIsNone(grp.extraer_area_pagina(texto))
        self.assertIsNone(grp.extraer_etiqueta_real(texto))

    def test_area_vacia_tras_el_dos_puntos(self):
        texto = "Area ...........:   \n"
        self.assertIsNone(grp.extraer_area_pagina(texto))

    def test_texto_ausente(self):
        self.assertIsNone(grp.extraer_area_pagina(""))
        self.assertIsNone(grp.extraer_area_pagina(None))


class ExtraerEtiquetaRealTests(unittest.TestCase):
    def test_linea_con_etiqueta(self):
        texto = "CONTENEDOR  ....:  H0                                           ETIQUETA   ....:   20021563094"
        self.assertEqual(grp.extraer_etiqueta_real(texto), "20021563094")

    def test_cabecera_de_columnas_sin_digitos(self):
        texto = "          ALBARAN ORIGEN     ETIQUETA ORIGEN    ALBARAN DESTINO    ETIQUETA DESTINO    TIPO REMONTE"
        self.assertIsNone(grp.extraer_etiqueta_real(texto))


class DetectarFormatoPdfTests(unittest.TestCase):
    def test_rf625a_por_identificador(self):
        self.assertEqual(grp.detectar_formato_pdf("... CPD-RF625A ..."), "RF625A")

    def test_rf625a_por_listado_cajas(self):
        self.assertEqual(grp.detectar_formato_pdf("LISTADO CAJAS DE PREPARACION"), "RF625A")

    def test_rf626a_por_identificador(self):
        self.assertEqual(grp.detectar_formato_pdf("... CPD-RF626A ..."), "RF626A")

    def test_desconocido_usa_por_defecto(self):
        self.assertEqual(grp.detectar_formato_pdf("texto sin identificador", por_defecto="RF626A"), "RF626A")
        self.assertIsNone(grp.detectar_formato_pdf("texto sin identificador"))


# ---------------------------------------------------------------------------
# Escritura de la tabla Metadata
# ---------------------------------------------------------------------------

class WriteMetadataTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.close()

    def test_escribe_las_cuatro_claves(self):
        grp.write_metadata(self.cur, "refrigerado", "Area=REFRIGERADO", "RF626A")
        self.conn.commit()
        meta = _metadata_dict(self.cur)
        self.assertEqual(
            meta,
            {
                "schema_version": "2",
                "grupo": "refrigerado",
                "grupo_fuente": "Area=REFRIGERADO",
                "formato_pdf": "RF626A",
            },
        )

    def test_grupo_invalido_cae_a_sin_clasificar(self):
        grp.write_metadata(self.cur, "no-es-un-grupo-valido", "Area=X", "RF626A")
        meta = _metadata_dict(self.cur)
        self.assertEqual(meta["grupo"], grp.SIN_CLASIFICAR)

    def test_grupo_nunca_vacio_ni_null(self):
        grp.write_metadata(self.cur, "", "", "")
        meta = _metadata_dict(self.cur)
        self.assertTrue(meta["grupo"])
        self.assertIn(meta["grupo"], grp.GRUPOS_VALIDOS)

    def test_reescritura_borra_valores_obsoletos(self):
        grp.write_metadata(self.cur, "refrigerado", "Area=REFRIGERADO", "RF626A")
        grp.write_metadata(self.cur, "congelado", "Area=CONGELADO", "RF626A")
        self.cur.execute("SELECT COUNT(*) FROM Metadata")
        self.assertEqual(self.cur.fetchone()[0], 4)
        meta = _metadata_dict(self.cur)
        self.assertEqual(meta["grupo"], "congelado")


# ---------------------------------------------------------------------------
# Integracion con los PDFs reales versionados (copiados a tempdir; nunca
# se ejecuta process_pdf() sobre el original).
# ---------------------------------------------------------------------------

# Divergencias conocidas y preexistentes entre los .db de referencia y el
# parser actual de main, confirmadas por git log/git show ANTES de esta
# rama (no introducidas por el modulo de grupo operativo):
#
#   etiqueta 20021577458: el .db de referencia se genero en el commit
#   inicial "58d4803 Primera version web del convertidor PDF a DB", cuyo
#   parse_side() exigia codigos de producto de al menos 3 digitos. El
#   commit "704ff03 Fix pallet entero and 2-digit codes" (ya fusionado en
#   main antes de crear esta rama) bajo ese minimo a 2 digitos. La fila
#   con codigo "84" (MANDARINA) es un producto de 2 digitos que el
#   parser actual reconoce correctamente; el .db de referencia, generado
#   antes de esa correccion, nunca se regenero y por tanto no la incluye.
#   No se toca el parser ni se regenera el .db de referencia: queda
#   documentado como riesgo preexistente (ver informe), fuera del
#   alcance de esta tarea.
FILAS_EXTRA_CONOCIDAS_PREEXISTENTES = {
    "20021577458": {("20021577458", "84", "MANDARINA", 1, 0)},
}

# etiqueta -> (grupo esperado, grupo_fuente esperado, formato_pdf esperado)
RESULTADO_ESPERADO = {
    "20021563094": ("refrigerado", "Area=REFRIGERADO", "RF626A"),
    "14196_0-610268": ("almacen_central", "Area=UNITARIO ILLESCAS", "RF625A"),
    "20021574815": ("congelado", "Area=CONGELADO", "RF626A"),
    "20021574816": ("congelado", "Area=CONGELADO", "RF626A"),
    "20021574913": ("refrigerado", "Area=REFRIGERADO", "RF626A"),
    "20021575328": ("seco", "Area=SECO", "RF626A"),
    "20021575329": ("seco", "Area=SECO", "RF626A"),
    "20021575330": ("seco", "Area=SECO", "RF626A"),
    "20021577458": ("fruta_verdura", "Area=FRUTA", "RF626A"),
    "20021577459": ("fruta_verdura", "Area=FRUTA", "RF626A"),
    "20021577843": ("pollo_carne", "Area=FLUJO TENSO", "RF626A"),
    "20021577844": ("pollo_carne", "Area=FLUJO TENSO", "RF626A"),
    "730009324222": ("almacen_central", "Area=SECO ILLESCAS", "RF626A"),
}


class IntegracionPdfsRealesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdfs_originales = _git_ls_files("Tiendas/**/pdfs/*.pdf")
        cls.dbs_referencia = _git_ls_files("Tiendas/**/db/*.db")
        assert cls.pdfs_originales, "No se encontraron PDFs versionados via git ls-files"
        assert cls.dbs_referencia, "No se encontraron .db versionados via git ls-files"

        cls._tmp = tempfile.TemporaryDirectory()
        tmp_root = Path(cls._tmp.name)
        cls.entrada_dir = tmp_root / "entrada"
        cls.salida_dir = tmp_root / "salida"
        cls.entrada_dir.mkdir()
        cls.salida_dir.mkdir()

        # 1) indice de referencia: etiqueta -> ruta del .db versionado
        cls.referencia_por_etiqueta = {}
        for db_path in cls.dbs_referencia:
            conn = _read_db_ro(db_path)
            try:
                cur = conn.cursor()
                cur.execute("SELECT Etiqueta FROM Etiqueta")
                (etiqueta,) = cur.fetchone()
                cls.referencia_por_etiqueta[etiqueta] = db_path
            finally:
                conn.close()

        # 2) procesar copias temporales de los 8 PDFs reales
        cls.generados = {}  # etiqueta -> Path del .db generado
        for pdf_original in cls.pdfs_originales:
            copia = cls.entrada_dir / pdf_original.name
            shutil.copy2(pdf_original, copia)

            tipo = conversion_service.detectar_tipo_pdf(copia)
            if tipo == "rf625a":
                resultado = cajas_azules.process_pdf(copia, cls.salida_dir)
                assert resultado["ok"], resultado
                db_path = Path(resultado["db_saved"])
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute("SELECT Etiqueta FROM Etiqueta")
                (etiqueta,) = cur.fetchone()
                conn.close()
                cls.generados[etiqueta] = db_path
            else:
                _, _, _, generados = batch_convert.process_pdf(copia, cls.salida_dir)
                for db_path in generados:
                    conn = sqlite3.connect(db_path)
                    cur = conn.cursor()
                    cur.execute("SELECT Etiqueta FROM Etiqueta")
                    (etiqueta,) = cur.fetchone()
                    conn.close()
                    cls.generados[etiqueta] = db_path

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_se_generan_las_13_etiquetas_esperadas(self):
        self.assertEqual(set(self.generados.keys()), set(RESULTADO_ESPERADO.keys()))
        self.assertEqual(len(self.generados), 13)

    def test_no_se_genera_etiqueta_espuria_00000000000(self):
        self.assertNotIn("00000000000", self.generados)

    def test_grupo_y_fuente_por_etiqueta(self):
        for etiqueta, (grupo_esp, fuente_esp, formato_esp) in RESULTADO_ESPERADO.items():
            with self.subTest(etiqueta=etiqueta):
                db_path = self.generados[etiqueta]
                conn = sqlite3.connect(db_path)
                try:
                    meta = _metadata_dict(conn.cursor())
                finally:
                    conn.close()
                self.assertEqual(meta["grupo"], grupo_esp)
                self.assertEqual(meta["grupo_fuente"], fuente_esp)
                self.assertEqual(meta["formato_pdf"], formato_esp)
                self.assertEqual(meta["schema_version"], "2")
                self.assertIn(meta["grupo"], grp.GRUPOS_VALIDOS)

    def test_compatibilidad_select_antiguo_sobre_linea(self):
        # La consulta que ya usa RepasoAlbaranes debe seguir funcionando
        # exactamente igual sobre los .db nuevos.
        for etiqueta, db_path in self.generados.items():
            with self.subTest(etiqueta=etiqueta):
                conn = sqlite3.connect(db_path)
                try:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT Etiqueta, Codigo, Descripcion, Cantidad, Falta "
                        "FROM Linea ORDER BY id"
                    )
                    filas = cur.fetchall()
                finally:
                    conn.close()
                self.assertGreater(len(filas), 0)
                for fila in filas:
                    self.assertEqual(fila[0], etiqueta)
                    self.assertEqual(fila[4], 0)  # Falta sigue sin tocarse

    def test_filas_generadas_coinciden_con_los_db_de_referencia(self):
        for etiqueta, db_generado in self.generados.items():
            with self.subTest(etiqueta=etiqueta):
                db_referencia = self.referencia_por_etiqueta.get(etiqueta)
                self.assertIsNotNone(
                    db_referencia, f"No hay .db de referencia para etiqueta {etiqueta}"
                )

                conn_gen = sqlite3.connect(db_generado)
                conn_ref = _read_db_ro(db_referencia)
                try:
                    extra_conocida = FILAS_EXTRA_CONOCIDAS_PREEXISTENTES.get(etiqueta, set())
                    extra_conocida_ctr = Counter(extra_conocida)

                    # Counter, no set: si una fila aparece repetida un numero
                    # distinto de veces en el generado que en la referencia
                    # (duplicado inesperado, o fila perdida entre repetidas),
                    # un set lo ocultaria por igualdad de pertenencia; Counter
                    # lo detecta por multiplicidad exacta.
                    filas_gen = Counter(_linea_rows(conn_gen.cursor()))
                    filas_ref = Counter(_linea_rows(conn_ref.cursor()))

                    faltantes = filas_ref - filas_gen
                    self.assertEqual(
                        faltantes, Counter(),
                        f"Filas de referencia ausentes o con menos repeticiones en el generado: {faltantes}",
                    )

                    sobrantes = filas_gen - filas_ref
                    self.assertEqual(
                        sobrantes, extra_conocida_ctr,
                        f"Filas extra no documentadas como divergencia preexistente: {sobrantes - extra_conocida_ctr}",
                    )

                    codigos_extra_conocidos = Counter(fila[1] for fila in extra_conocida)
                    codigos_gen = Counter(r[0] for r in conn_gen.cursor().execute("SELECT Codigo FROM Codigo"))
                    codigos_ref = Counter(r[0] for r in conn_ref.cursor().execute("SELECT Codigo FROM Codigo"))
                    self.assertEqual(codigos_ref - codigos_gen, Counter())
                    self.assertEqual(codigos_gen - codigos_ref, codigos_extra_conocidos)

                    descr_extra_conocidas = Counter(fila[2] for fila in extra_conocida)
                    descr_gen = Counter(
                        r[0] for r in conn_gen.cursor().execute("SELECT Descripcion FROM Descripcion")
                    )
                    descr_ref = Counter(
                        r[0] for r in conn_ref.cursor().execute("SELECT Descripcion FROM Descripcion")
                    )
                    self.assertEqual(descr_ref - descr_gen, Counter())
                    self.assertEqual(descr_gen - descr_ref, descr_extra_conocidas)

                    etq_gen = conn_gen.cursor().execute("SELECT Etiqueta FROM Etiqueta").fetchall()
                    etq_ref = conn_ref.cursor().execute("SELECT Etiqueta FROM Etiqueta").fetchall()
                    self.assertEqual(etq_gen, etq_ref)
                finally:
                    conn_gen.close()
                    conn_ref.close()

    def test_db_de_referencia_no_tienen_metadata_schema_v1(self):
        # Documenta el estado de partida: los .db versionados son
        # anteriores a este cambio y no tienen tabla Metadata.
        for etiqueta, db_path in self.referencia_por_etiqueta.items():
            with self.subTest(etiqueta=etiqueta):
                conn = _read_db_ro(db_path)
                try:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='Metadata'"
                    )
                    self.assertIsNone(cur.fetchone())
                finally:
                    conn.close()

    def test_pdfs_originales_no_se_movieron(self):
        for pdf_path in self.pdfs_originales:
            self.assertTrue(pdf_path.exists(), f"El PDF original desaparecio: {pdf_path}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
