"""
Pruebas de services/db_fingerprint.py: huella lógica determinista y
versionada de un .db del convertidor. Todos los .db se crean dentro de
TemporaryDirectory con los escritores reales (batch_convert.write_db /
cajas_azules.write_db) o con SQL explícito; nunca se toca un .db
versionado del repositorio.
"""

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

import batch_convert
import cajas_azules
from services.db_fingerprint import (
    HUELLA_PREFIJO,
    HuellaError,
    _codificar_valor,
    es_huella_actual,
    huella_logica_de_bytes,
    huella_logica_de_ruta,
)

ACC = {("100", "LECHE", "bulto"): 6, ("200", "AGUA", "bulto"): 3}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def escribir(self, nombre, acc=ACC, etiqueta="E1", grupo="seco", fuente="AREA", formato="RF626A", writer=batch_convert):
        ruta = self.dir / nombre
        writer.write_db(etiqueta, dict(acc), str(ruta), grupo, fuente, formato)
        return ruta

    def ejecutar_sql(self, ruta, *sentencias):
        conn = sqlite3.connect(ruta)
        try:
            for sql, params in sentencias:
                conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()


class HuellaEstableTests(_Base):
    def test_formato_versionado(self):
        huella = huella_logica_de_ruta(self.escribir("a.db"))
        self.assertTrue(huella.startswith(HUELLA_PREFIJO))
        self.assertTrue(es_huella_actual(huella))
        self.assertEqual(len(huella), len(HUELLA_PREFIJO) + 64)

    def test_misma_entrada_misma_huella_en_archivos_distintos(self):
        a = huella_logica_de_ruta(self.escribir("a.db"))
        b = huella_logica_de_ruta(self.escribir("b.db"))
        self.assertEqual(a, b)

    def test_reescribir_la_misma_ruta_cambia_ids_pero_no_la_huella(self):
        # Mismo patrón que un lote que reutiliza la ruta: DELETE + INSERT
        # con AUTOINCREMENT -> ids nuevos y bytes distintos.
        ruta = self.escribir("a.db")
        huella_1 = huella_logica_de_ruta(ruta)
        bytes_1 = ruta.read_bytes()
        conn = sqlite3.connect(ruta)
        ids_1 = [r[0] for r in conn.execute("SELECT id FROM Linea ORDER BY id")]
        conn.close()

        self.escribir("a.db")
        conn = sqlite3.connect(ruta)
        ids_2 = [r[0] for r in conn.execute("SELECT id FROM Linea ORDER BY id")]
        conn.close()

        self.assertNotEqual(ids_1, ids_2)
        self.assertNotEqual(bytes_1, ruta.read_bytes())
        self.assertEqual(huella_1, huella_logica_de_ruta(ruta))

    def test_orden_de_insercion_no_cambia_la_huella(self):
        a = huella_logica_de_ruta(self.escribir("a.db", acc=ACC))
        invertido = dict(reversed(list(ACC.items())))
        b = huella_logica_de_ruta(self.escribir("b.db", acc=invertido))
        self.assertEqual(a, b)

    def test_huella_de_bytes_igual_a_huella_de_ruta(self):
        ruta = self.escribir("a.db")
        self.assertEqual(huella_logica_de_ruta(ruta), huella_logica_de_bytes(ruta.read_bytes()))

    def test_escritor_de_cajas_azules_tambien_es_estable(self):
        a = huella_logica_de_ruta(self.escribir("a.db", writer=cajas_azules, formato="RF625A"))
        b = huella_logica_de_ruta(self.escribir("b.db", writer=cajas_azules, formato="RF625A"))
        self.assertEqual(a, b)

    def test_valor_de_referencia_fijo_de_v1(self):
        # Fija el formato canónico v1: si alguien cambia la
        # serialización sin subir la versión, esta prueba falla.
        ruta = self.dir / "ref.db"
        self.ejecutar_sql(
            ruta,
            ("CREATE TABLE Etiqueta (Etiqueta TEXT PRIMARY KEY)", ()),
            ("CREATE TABLE Linea (id INTEGER PRIMARY KEY AUTOINCREMENT, Codigo TEXT, Cantidad INTEGER, Falta INTEGER DEFAULT 0)", ()),
            ("INSERT INTO Etiqueta VALUES ('E1')", ()),
            ("INSERT INTO Linea (Codigo, Cantidad, Falta) VALUES ('1', 2, NULL)", ()),
        )
        self.assertEqual(huella_logica_de_ruta(ruta), HUELLA_REFERENCIA_V1)


class HuellaSensibleTests(_Base):
    def setUp(self):
        super().setUp()
        self.base = huella_logica_de_ruta(self.escribir("base.db"))

    def _modificada(self, *sentencias):
        ruta = self.escribir("mod.db")
        self.ejecutar_sql(ruta, *sentencias)
        return huella_logica_de_ruta(ruta)

    def test_cantidad_distinta(self):
        self.assertNotEqual(self.base, huella_logica_de_ruta(
            self.escribir("c.db", acc={("100", "LECHE", "bulto"): 7, ("200", "AGUA", "bulto"): 3})
        ))

    def test_falta_distinta(self):
        self.assertNotEqual(self.base, self._modificada(
            ("UPDATE Linea SET Falta = 1 WHERE Codigo = '100'", ()),
        ))

    def test_falta_null_distinta_de_cero(self):
        self.assertNotEqual(self.base, self._modificada(
            ("UPDATE Linea SET Falta = NULL WHERE Codigo = '100'", ()),
        ))

    def test_multiplicidad_de_filas_de_linea(self):
        # Una fila repetida debe contar: con set() sería invisible.
        self.assertNotEqual(self.base, self._modificada(
            ("INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad, Falta, TipoMedida) "
             "SELECT Etiqueta, Codigo, Descripcion, Cantidad, Falta, TipoMedida FROM Linea WHERE Codigo = '100'", ()),
        ))

    def test_dos_copias_frente_a_tres_copias(self):
        dos = self._modificada(
            ("INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad, Falta, TipoMedida) VALUES ('E1','100','LECHE',6,0,'bulto')", ()),
        )
        ruta = self.escribir("tres.db")
        self.ejecutar_sql(
            ruta,
            ("INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad, Falta, TipoMedida) VALUES ('E1','100','LECHE',6,0,'bulto')", ()),
            ("INSERT INTO Linea (Etiqueta, Codigo, Descripcion, Cantidad, Falta, TipoMedida) VALUES ('E1','100','LECHE',6,0,'bulto')", ()),
        )
        self.assertNotEqual(dos, huella_logica_de_ruta(ruta))

    def test_metadata_grupo_distinto(self):
        self.assertNotEqual(self.base, huella_logica_de_ruta(self.escribir("g.db", grupo="congelado")))

    def test_metadata_formato_distinto(self):
        self.assertNotEqual(self.base, huella_logica_de_ruta(self.escribir("f.db", formato="RF625A")))

    def test_metadata_ausente_distinta_de_metadata_vacia(self):
        sin = self._modificada(("DROP TABLE Metadata", ()))
        vacia = self._modificada(("DELETE FROM Metadata", ()))
        self.assertNotEqual(sin, vacia)
        self.assertNotEqual(sin, self.base)

    def test_etiqueta_distinta(self):
        self.assertNotEqual(self.base, huella_logica_de_ruta(self.escribir("e.db", etiqueta="E2")))

    def test_tabla_codigo_y_descripcion_cuentan(self):
        self.assertNotEqual(self.base, self._modificada(("INSERT INTO Codigo VALUES ('999')", ())))
        self.assertNotEqual(self.base, self._modificada(("INSERT INTO Descripcion VALUES ('OTRA')", ())))

    def test_tabla_ajena_a_la_huella_no_cuenta(self):
        self.assertEqual(self.base, self._modificada(("CREATE TABLE Otra (x TEXT)", ()), ("INSERT INTO Otra VALUES ('1')", ())))


class CodificacionTests(unittest.TestCase):
    def test_null_vacio_y_cero_son_distintos(self):
        codigos = {repr(_codificar_valor(v)) for v in (None, "", 0, 0.0, b"")}
        self.assertEqual(len(codigos), 5)

    def test_texto_numerico_distinto_de_entero(self):
        self.assertNotEqual(_codificar_valor("1"), _codificar_valor(1))


class LecturaSeguraTests(_Base):
    def test_no_modifica_el_archivo(self):
        ruta = self.escribir("a.db")
        antes = _sha(ruta)
        huella_logica_de_ruta(ruta)
        self.assertEqual(antes, _sha(ruta))

    def test_no_crea_un_db_inexistente(self):
        ruta = self.dir / "no_existe.db"
        with self.assertRaises(HuellaError):
            huella_logica_de_ruta(ruta)
        self.assertFalse(ruta.exists())

    def test_bytes_vacios_o_invalidos_lanzan_error(self):
        with self.assertRaises(HuellaError):
            huella_logica_de_bytes(b"")
        with self.assertRaises(HuellaError):
            huella_logica_de_bytes(b"esto no es sqlite" * 100)

    def test_es_huella_actual_rechaza_otras_versiones(self):
        self.assertFalse(es_huella_actual(None))
        self.assertFalse(es_huella_actual(""))
        self.assertFalse(es_huella_actual("v0:abc"))
        self.assertFalse(es_huella_actual(HUELLA_PREFIJO))


HUELLA_REFERENCIA_V1 = "v1:fe511038ae5f5ec1671597010d1fa3ace2133bd1220acf7cbf14c959b811daca"


if __name__ == "__main__":
    unittest.main(verbosity=2)
