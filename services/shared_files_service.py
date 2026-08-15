"""
Validación y utilidades de los archivos compartidos (APK/PDF) de la
pestaña "Compartir APK/PDF". No toca conversion_service.py ni los
scripts del convertidor: es un dominio aparte.

Todo el contenido se trata como bytes opacos: nunca se extrae, ejecuta
ni interpreta. La validación de APK confirma que es un ZIP reconocible
y que contiene AndroidManifest.xml, sin descomprimir nada a disco.
"""

import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

MAX_SHARED_FILE_BYTES = 150 * 1024 * 1024  # 150 MB

CONTENT_TYPES = {
    "apk": "application/vnd.android.package-archive",
    "pdf": "application/pdf",
}

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


class ArchivoInvalido(Exception):
    """Un archivo subido no supera la validación de servidor."""


def sanear_nombre_archivo(nombre: str) -> str:
    """
    Se queda solo con el nombre de archivo, descartando cualquier ruta
    incrustada, y elimina caracteres de control.

    OJO: Path(nombre).name (la forma que usa conversion_service.py para
    los PDF del convertidor) solo reconoce '\\' como separador cuando el
    proceso corre en Windows (WindowsPath); en un servidor Linux/Docker
    Path resuelve a PosixPath, que trata '\\' como un carácter normal, y
    "C:\\Windows\\evil.pdf" pasaría intacto. Aquí se normalizan AMBOS
    separadores ('/' y '\\') de forma explícita con PurePosixPath, cuyo
    comportamiento no depende del sistema operativo donde se ejecute.
    """
    nombre = (nombre or "").replace("\\", "/")
    nombre = PurePosixPath(nombre).name
    nombre = _CONTROL_CHARS_RE.sub("", nombre)
    nombre = nombre.strip()
    return nombre or "archivo"


def detectar_file_kind(nombre_saneado: str) -> str:
    sufijo = Path(nombre_saneado).suffix.lower()
    if sufijo == ".apk":
        return "apk"
    if sufijo == ".pdf":
        return "pdf"
    raise ArchivoInvalido(f"Extensión no permitida: {sufijo or '(sin extensión)'}")


def validar_tamano(size_bytes) -> None:
    if size_bytes is None or size_bytes <= 0:
        raise ArchivoInvalido("El archivo está vacío.")
    if size_bytes > MAX_SHARED_FILE_BYTES:
        limite_mb = MAX_SHARED_FILE_BYTES // (1024 * 1024)
        raise ArchivoInvalido(
            f"El archivo supera el tamaño máximo permitido ({limite_mb} MB)."
        )


def validar_pdf(fileobj) -> None:
    """PDF válido = empieza por la cabecera %PDF- (comprobación real de
    contenido, no solo de extensión)."""
    fileobj.seek(0)
    cabecera = fileobj.read(5)
    fileobj.seek(0)
    if cabecera != b"%PDF-":
        raise ArchivoInvalido("El archivo no es un PDF válido (no empieza por %PDF-).")


def validar_apk(fileobj) -> None:
    """
    Un APK es un ZIP, pero no basta con comprobar la cabecera 'PK': se
    exige además que zipfile lo reconozca como ZIP válido Y que
    contenga AndroidManifest.xml. Nunca se extrae ningún miembro.
    """
    fileobj.seek(0)
    if not zipfile.is_zipfile(fileobj):
        fileobj.seek(0)
        raise ArchivoInvalido("El archivo no es un APK válido (no es un ZIP reconocible).")

    fileobj.seek(0)
    try:
        with zipfile.ZipFile(fileobj) as zf:
            nombres = zf.namelist()
    except zipfile.BadZipFile as exc:
        raise ArchivoInvalido("El archivo ZIP está corrupto.") from exc
    finally:
        fileobj.seek(0)

    if "AndroidManifest.xml" not in nombres:
        raise ArchivoInvalido(
            "El archivo ZIP no contiene AndroidManifest.xml: no parece un APK."
        )


def validar_archivo_compartido(nombre_original: str, size_bytes, fileobj) -> dict:
    """
    Valida por completo un archivo subido para la pestaña de compartidos:
    nombre saneado, extensión permitida, tamaño y contenido real
    (cabecera PDF o estructura de APK). Lanza ArchivoInvalido con un
    mensaje explicando qué falló y cómo corregirlo si no pasa alguna
    comprobación. Deja fileobj posicionado en 0 al terminar, listo para
    subir a R2 sin volver a leerlo primero.
    """
    nombre_saneado = sanear_nombre_archivo(nombre_original)
    file_kind = detectar_file_kind(nombre_saneado)
    validar_tamano(size_bytes)

    if file_kind == "pdf":
        validar_pdf(fileobj)
    else:
        validar_apk(fileobj)

    fileobj.seek(0)

    return {
        "file_kind": file_kind,
        "original_file_name": nombre_saneado,
        "content_type": CONTENT_TYPES[file_kind],
    }


def build_shared_object_key(store_code: str, file_kind: str, nombre_saneado: str) -> str:
    """
    Prefijo 'shared/' deliberadamente distinto de 'stores/' (el que ya
    usa r2_service.build_object_key para los .db del convertidor), para
    no mezclar espacios de nombres y poder aplicar reglas de lifecycle
    de R2 por prefijo en el futuro si se decide.
    """
    now = datetime.now(timezone.utc)
    random_part = uuid4().hex
    return f"shared/{file_kind}/{store_code}/{now:%Y/%m/%d}/{random_part}_{nombre_saneado}"
