"""
Pruebas de services.presigned_url_cache: cálculo de caducidad con
margen de seguridad, y validez de una entrada guardada. Sin Streamlit,
sin red — todo con instantes fijos pasados explícitamente.
"""

import unittest
from datetime import datetime, timedelta, timezone

from services.presigned_url_cache import (
    MARGEN_SEGURIDAD_SEGUNDOS,
    calcular_expiracion,
    construir_entrada,
    entrada_valida,
)

AHORA = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)


class CalcularExpiracionTests(unittest.TestCase):
    def test_resta_el_margen_de_seguridad(self):
        resultado = calcular_expiracion(900, ahora=AHORA)
        esperado = AHORA + timedelta(seconds=900 - MARGEN_SEGURIDAD_SEGUNDOS)
        self.assertEqual(resultado, esperado)

    def test_resultado_tiene_zona_horaria(self):
        resultado = calcular_expiracion(900, ahora=AHORA)
        self.assertIsNotNone(resultado.tzinfo)

    def test_expires_in_muy_pequeno_no_da_fecha_negativa_respecto_a_ahora(self):
        # Con expires_in menor que el margen, no debe "caducar antes de
        # calcularse": el margen aplicado nunca es mayor que expires_in.
        resultado = calcular_expiracion(10, ahora=AHORA)
        self.assertGreaterEqual(resultado, AHORA)


class ConstruirEntradaTests(unittest.TestCase):
    def test_incluye_url_y_expiracion(self):
        entrada = construir_entrada("https://r2.example/a.db", 900, ahora=AHORA)
        self.assertEqual(entrada["url"], "https://r2.example/a.db")
        self.assertEqual(entrada["expira_en"], AHORA + timedelta(seconds=900 - MARGEN_SEGURIDAD_SEGUNDOS))


class EntradaValidaTests(unittest.TestCase):
    def test_entrada_recien_creada_es_valida(self):
        entrada = construir_entrada("https://r2.example/a.db", 900, ahora=AHORA)
        self.assertTrue(entrada_valida(entrada, ahora=AHORA))

    def test_entrada_todavia_dentro_del_plazo(self):
        entrada = construir_entrada("https://r2.example/a.db", 900, ahora=AHORA)
        diez_minutos_despues = AHORA + timedelta(minutes=10)
        self.assertTrue(entrada_valida(entrada, ahora=diez_minutos_despues))

    def test_entrada_caducada_tras_pasar_el_margen(self):
        entrada = construir_entrada("https://r2.example/a.db", 900, ahora=AHORA)
        # 900s - margen ya transcurridos: debe considerarse caducada.
        despues_del_margen = AHORA + timedelta(seconds=900 - MARGEN_SEGURIDAD_SEGUNDOS + 1)
        self.assertFalse(entrada_valida(entrada, ahora=despues_del_margen))

    def test_entrada_caduca_ANTES_de_que_r2_la_rechace_de_verdad(self):
        # El propósito del margen: la entrada debe caducar en nuestro
        # lado con margen suficiente para nunca ofrecer un enlace que R2
        # ya vaya a rechazar en el instante justo de la firma real.
        entrada = construir_entrada("https://r2.example/a.db", 900, ahora=AHORA)
        instante_real_de_caducidad_en_r2 = AHORA + timedelta(seconds=900)
        self.assertFalse(entrada_valida(entrada, ahora=instante_real_de_caducidad_en_r2))

    def test_entrada_none_no_es_valida(self):
        self.assertFalse(entrada_valida(None, ahora=AHORA))

    def test_entrada_vacia_no_es_valida(self):
        self.assertFalse(entrada_valida({}, ahora=AHORA))

    def test_entrada_mal_formada_no_es_valida(self):
        self.assertFalse(entrada_valida({"url": "x"}, ahora=AHORA))
        self.assertFalse(entrada_valida({"expira_en": AHORA}, ahora=AHORA))


class ConstanteDeDuracionCompartidaTests(unittest.TestCase):
    """
    La duración real de la firma (generate_download_url(expires_in=...))
    y la duración con la que se calcula la caducidad registrada
    (construir_entrada(...)) deben venir de la MISMA constante, para que
    no puedan desincronizarse silenciosamente si alguien cambia una sin
    tocar la otra.

    "Archivos 24h" se simplificó y ya no usa URLs prefirmadas en
    absoluto (services/presigned_url_cache no se importa desde
    ui/files_tab.py): solo "Compartir APK/PDF" (ui/shared_tab.py) sigue
    necesitando esta gestión de caducidad, porque ahí sí puede haber
    archivos grandes.
    """

    def test_shared_tab_importa_la_misma_constante(self):
        import ui.shared_tab as shared_tab
        from services.presigned_url_cache import URL_EXPIRES_IN_SEGUNDOS

        self.assertIs(shared_tab.URL_EXPIRES_IN_SEGUNDOS, URL_EXPIRES_IN_SEGUNDOS)

    def test_files_tab_ya_no_usa_urls_prefirmadas(self):
        import ui.files_tab as files_tab

        self.assertFalse(hasattr(files_tab, "URL_EXPIRES_IN_SEGUNDOS"))
        self.assertFalse(hasattr(files_tab, "generate_download_url"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
