"""
Pruebas de ui.formatting.format_dt: la conversión debe ser siempre a
Europe/Madrid explícitamente (vía services.time_utils.APP_TZ), nunca a
la zona horaria del servidor (dt.astimezone() sin argumento). Los
valores ISO de entrada van siempre en UTC (así los devuelve Supabase),
así que estas pruebas no dependen de la zona horaria configurada en
Windows, en el servidor ni en ningún otro sitio: el resultado esperado
se calcula a mano a partir del desfase real de Madrid en cada época del
año.
"""

import unittest

from ui.formatting import format_dt


class FormatDtTests(unittest.TestCase):
    def test_verano_utc_mas_2(self):
        # Madrid en verano es UTC+2 (CEST): 22:00 UTC del día 15 son las
        # 00:00 del día 16 en Madrid.
        self.assertEqual(format_dt("2026-08-15T22:00:00+00:00"), "16/08/2026 00:00")

    def test_invierno_utc_mas_1(self):
        # Madrid en invierno es UTC+1 (CET): 23:00 UTC del día 15 son
        # las 00:00 del día 16 en Madrid.
        self.assertEqual(format_dt("2026-01-15T23:00:00+00:00"), "16/01/2026 00:00")

    def test_valor_terminado_en_z_sigue_funcionando(self):
        self.assertEqual(format_dt("2026-01-15T23:00:00Z"), "16/01/2026 00:00")

    def test_verano_terminado_en_z(self):
        self.assertEqual(format_dt("2026-08-15T22:00:00Z"), "16/08/2026 00:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
