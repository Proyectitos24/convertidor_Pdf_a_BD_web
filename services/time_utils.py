"""
Utilidad de tiempo compartida por "Archivos 24h" (converted_files) y
"Compartir APK/PDF" (shared_files): ambos caducan a la medianoche de
Europe/Madrid del día siguiente a la subida, no en una ventana móvil de
24 horas exactas. Antes vivía solo dentro de app.py; se centraliza aquí
para no duplicarla entre las dos pestañas.
"""

from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

APP_TZ = ZoneInfo("Europe/Madrid")


def next_midnight_europe_madrid() -> datetime:
    """
    Devuelve, en UTC, el instante de las 00:00 de Europe/Madrid del día
    siguiente al momento actual. zoneinfo resuelve el cambio de horario
    de verano/invierno automáticamente: no hace falta lógica aparte para
    los días en que Madrid cambia de UTC+1 a UTC+2 o viceversa.
    """
    now_local = datetime.now(APP_TZ)
    tomorrow = now_local.date() + timedelta(days=1)
    midnight_local = datetime.combine(tomorrow, time.min, tzinfo=APP_TZ)
    return midnight_local.astimezone(timezone.utc)
