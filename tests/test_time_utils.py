"""
Pruebas de services.time_utils.next_midnight_europe_madrid(), incluidos
los cambios de horario de verano/invierno en Europe/Madrid. No se
conecta a ningún servicio: solo fija el "ahora" con una subclase de
datetime, sin dependencias nuevas (sin freezegun).

Los valores esperados de estas pruebas se verificaron primero
ejecutando la función real contra estos mismos instantes fijos, no se
inventaron a mano.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from services import time_utils


class _FixedDateTime(datetime):
    fixed_now = None

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return cls.fixed_now.astimezone(tz)
        return cls.fixed_now


class NextMidnightEuropeMadridTests(unittest.TestCase):
    def _con_ahora_fijado(self, fixed_dt_utc):
        _FixedDateTime.fixed_now = fixed_dt_utc
        return mock.patch.object(time_utils, "datetime", _FixedDateTime)

    def test_invierno_madrid_utc_mas_1(self):
        # 10 de enero de 2026, 15:00 UTC. Madrid está en CET (UTC+1).
        fijo = datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc)
        with self._con_ahora_fijado(fijo):
            resultado = time_utils.next_midnight_europe_madrid()
        self.assertEqual(resultado, datetime(2026, 1, 10, 23, 0, tzinfo=timezone.utc))

    def test_verano_madrid_utc_mas_2(self):
        # 10 de julio de 2026, 15:00 UTC. Madrid está en CEST (UTC+2).
        fijo = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)
        with self._con_ahora_fijado(fijo):
            resultado = time_utils.next_midnight_europe_madrid()
        self.assertEqual(resultado, datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc))

    def test_dia_del_cambio_de_horario_sigue_en_utc_mas_1_a_medianoche(self):
        # 28 de marzo de 2026, 10:00 UTC — la víspera del cambio de
        # invierno a verano en la UE. A medianoche del 29 (hora Madrid)
        # el cambio TODAVÍA no ha ocurrido (ocurre sobre las 02:00 hora
        # local del propio 29), así que sigue en CET (UTC+1).
        fijo = datetime(2026, 3, 28, 10, 0, tzinfo=timezone.utc)
        with self._con_ahora_fijado(fijo):
            resultado = time_utils.next_midnight_europe_madrid()
        self.assertEqual(resultado, datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc))

    def test_resultado_siempre_en_utc(self):
        fijo = datetime(2026, 5, 1, 5, 0, tzinfo=timezone.utc)
        with self._con_ahora_fijado(fijo):
            resultado = time_utils.next_midnight_europe_madrid()
        self.assertEqual(resultado.tzinfo, timezone.utc)

    def test_resultado_es_medianoche_exacta_en_hora_de_madrid(self):
        fijo = datetime(2026, 5, 1, 5, 0, tzinfo=timezone.utc)
        with self._con_ahora_fijado(fijo):
            resultado = time_utils.next_midnight_europe_madrid()
        local = resultado.astimezone(time_utils.APP_TZ)
        self.assertEqual((local.hour, local.minute, local.second), (0, 0, 0))

    def test_resultado_es_siempre_el_dia_siguiente_al_actual_en_madrid(self):
        fijo = datetime(2026, 5, 1, 5, 0, tzinfo=timezone.utc)
        with self._con_ahora_fijado(fijo):
            ahora_local = fijo.astimezone(time_utils.APP_TZ)
            resultado = time_utils.next_midnight_europe_madrid()
        local = resultado.astimezone(time_utils.APP_TZ)
        self.assertEqual(local.date(), ahora_local.date() + timedelta(days=1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
