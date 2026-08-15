"""Formato de fechas para la interfaz. Extraído de app.py sin cambios de lógica."""

from datetime import datetime

from services.time_utils import APP_TZ


def format_dt(value: str) -> str:
    """
    Convierte siempre a Europe/Madrid explícitamente con APP_TZ, nunca
    con dt.astimezone() a secas: ese uso depende de la zona horaria del
    servidor, y en Streamlit Cloud (normalmente en UTC) mostraría, p.ej.,
    22:00 en vez de las 00:00 de Madrid que corresponde de verdad.
    """
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    dt = dt.astimezone(APP_TZ)
    return dt.strftime("%d/%m/%Y %H:%M")
