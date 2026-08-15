"""
Gestión de URLs prefirmadas de R2 con caducidad explícita, para no
dejar mostrado indefinidamente un enlace de descarga ya vencido. R2
firma por defecto durante 15 minutos
(services.r2_service.generate_download_url(expires_in=900)); sin esto,
una URL guardada sin más en st.session_state seguiría enseñándose tras
esos 15 minutos aunque ya no funcione.

No usa Streamlit ni red: opera sobre datos puros (normalmente el valor
guardado bajo una clave de st.session_state, que el llamador gestiona).
Usado por ui/shared_tab.py (descarga directa de APK/PDF mediante URL
prefirmada). "Archivos 24h" (ui/files_tab.py) no usa URLs prefirmadas:
descarga los bytes del .db directamente con st.download_button.

Nunca se guardan URLs prefirmadas en Supabase: solo viven en memoria de
sesión de Streamlit (st.session_state), que además es por-usuario y
temporal.
"""

from datetime import datetime, timedelta, timezone

# Duración por defecto (segundos) de las URLs prefirmadas de descarga
# de ui/shared_tab.py. Vive aquí, en un único sitio, para que la
# duración REAL de la firma (generate_download_url(expires_in=...)) y
# la duración con la que se calcula la caducidad registrada
# (construir_entrada(...)) no puedan desincronizarse silenciosamente.
URL_EXPIRES_IN_SEGUNDOS = 900

# Margen de seguridad: una entrada se considera caducada un poco ANTES
# de que la firma real de R2 deje de funcionar, para no ofrecer nunca
# un enlace que el servidor ya va a rechazar.
MARGEN_SEGURIDAD_SEGUNDOS = 30


def calcular_expiracion(expires_in_segundos, ahora=None):
    """
    Instante (UTC, consciente de zona horaria) en el que debe tratarse
    como caducada una URL firmada durante `expires_in_segundos`.
    """
    ahora = ahora if ahora is not None else datetime.now(timezone.utc)
    margen = min(MARGEN_SEGURIDAD_SEGUNDOS, max(expires_in_segundos - 1, 0))
    return ahora + timedelta(seconds=expires_in_segundos - margen)


def construir_entrada(url, expires_in_segundos, ahora=None):
    """Empaqueta una URL junto con su instante de caducidad (UTC)."""
    return {"url": url, "expira_en": calcular_expiracion(expires_in_segundos, ahora)}


def entrada_valida(entrada, ahora=None):
    """
    entrada: dict {"url": str, "expira_en": datetime} o None/{}.
    True solo si existe y el instante actual es anterior a su
    caducidad (ya con el margen de seguridad aplicado).
    """
    if not entrada or "expira_en" not in entrada or "url" not in entrada:
        return False
    ahora = ahora if ahora is not None else datetime.now(timezone.utc)
    return ahora < entrada["expira_en"]
