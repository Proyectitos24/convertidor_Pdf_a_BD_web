"""
Deteccion y almacenamiento del grupo operativo (RepasoAlbaranes) a partir
del campo de cabecera "Area ...........: <VALOR>" de los listados RF625A/RF626A.

Este modulo es la unica fuente de verdad para:
  - normalizar el texto de Area;
  - extraer la linea de Area de la cabecera de una pagina (nunca busca
    palabras clave sueltas en el cuerpo del documento);
  - mapear el valor normalizado al grupo canonico;
  - resolver conflictos cuando una misma etiqueta aparece con areas distintas;
  - crear/():escribir la tabla aditiva Metadata en el .db generado.

No clasifica por producto, codigo ni descripcion.
"""

import re

SCHEMA_VERSION = "2"

SIN_CLASIFICAR = "sin_clasificar"
FUENTE_AUSENTE = "NO_DETECTADO"

# Mapeo de negocio confirmado. Unica fuente de verdad: no lo dupliques.
#
# "SECO" (a secas) SI forma parte del mapeo, y resuelve al mismo grupo
# canonico que "SEMIPALETS": evidencia real encontrada en un mismo
# albaran de seco, donde distintas particiones/etiquetas del envio
# aparecen unas con Area=SEMIPALETS y otras con Area=SECO. Ambas se
# tratan como la misma "familia seco" a efectos de RepasoAlbaranes.
AREA_A_GRUPO = {
    "SECO ILLESCAS": "almacen_central",
    "UNITARIO ILLESCAS": "almacen_central",
    "FLUJO TENSO": "pollo_carne",
    "FRUTA": "fruta_verdura",
    "SEMIPALETS": "seco",
    "SECO": "seco",
    "REFRIGERADO": "refrigerado",
    "CONGELADO": "congelado",
}

GRUPOS_VALIDOS = frozenset(AREA_A_GRUPO.values()) | {SIN_CLASIFICAR}

# Orden y etiqueta visible para la interfaz (pestaña "Archivos 24h").
# Unica fuente de verdad para la presentacion del grupo: no la dupliques
# en app.py ni en los modulos de ui/.
GRUPO_ORDEN = [
    "almacen_central",
    "seco",
    "refrigerado",
    "congelado",
    "fruta_verdura",
    "pollo_carne",
    "sin_clasificar",
]

GRUPO_ETIQUETAS = {
    "almacen_central": "Almacén Central",
    "seco": "Seco",
    "refrigerado": "Refrigerado",
    "congelado": "Congelado",
    "fruta_verdura": "Fruta y Verdura",
    "pollo_carne": "Pollo/Carne",
    "sin_clasificar": "Sin clasificar",
}

assert set(GRUPO_ORDEN) == GRUPOS_VALIDOS, "GRUPO_ORDEN ha quedado desincronizado de GRUPOS_VALIDOS"
assert set(GRUPO_ETIQUETAS) == GRUPOS_VALIDOS, "GRUPO_ETIQUETAS ha quedado desincronizado de GRUPOS_VALIDOS"

# Ancla al campo "Area" al inicio de linea (permitiendo espacios de
# indentacion), con puntos y dos puntos de longitud variable. El valor
# termina antes de "PARTICION" o al final de la linea.
_AREA_LINEA_RE = re.compile(r"^\s*Area\s*\.*\s*:\s*(.+)$")
_PARTICION_RE = re.compile(r"\bPARTICION\b")

# Version "real" (sin valor por defecto) de la extraccion de ETIQUETA,
# usada solo para asociar Area<->Etiqueta por pagina. La extraccion de
# etiqueta usada para acumular productos (con su fallback historico a
# "00000000000") no se toca.
_ETIQUETA_RE = re.compile(r"ETIQUETA\s*\.*\s*:?\s*(\d{5,})")


def normalizar_area(valor):
    """Recorta espacios, colapsa espacios internos repetidos y pasa a mayusculas."""
    if not valor:
        return ""
    valor = valor.strip()
    valor = re.sub(r"\s+", " ", valor)
    return valor.upper()


def extraer_area_pagina(page_text):
    """
    Busca, linea a linea, la cabecera "Area ...........: <VALOR>".
    Nunca busca palabras clave (REFRIGERADO, FRUTA, SECO, ...) sueltas
    en el resto del texto de la pagina.

    Devuelve el valor de Area ya normalizado, o None si esa pagina no
    tiene una linea de Area reconocible.
    """
    if not page_text:
        return None

    for line in page_text.splitlines():
        m = _AREA_LINEA_RE.match(line)
        if not m:
            continue

        valor = _PARTICION_RE.split(m.group(1), maxsplit=1)[0]
        valor = normalizar_area(valor)
        if valor:
            return valor

    return None


def extraer_etiqueta_real(page_text):
    """
    Devuelve el numero de ETIQUETA de una pagina, o None si la pagina no
    tiene bloque de ETIQUETA (p.ej. una pagina de cierre "FIN LISTADO").
    A diferencia del extractor de etiqueta usado para acumular productos,
    esta version NO tiene un valor por defecto: se usa unicamente para
    decidir a que etiqueta asociar el Area encontrada en esa pagina.
    """
    if not page_text:
        return None
    m = _ETIQUETA_RE.search(page_text)
    return m.group(1) if m else None


def detectar_formato_pdf(texto, por_defecto=None):
    """
    Etiqueta informativa de formato para Metadata.formato_pdf, basada en
    los identificadores ya usados en el resto del repositorio
    (CPD-RF625A / "LISTADO CAJAS" para cajas azules, RF626A en otro caso).

    No reemplaza ni endurece el enrutamiento RF625A/RF626A existente en
    conversion_service.py / cajas_azules.is_rf625a(): es solo una
    etiqueta de auditoria para el .db.
    """
    texto = texto or ""
    if "RF625A" in texto or "LISTADO CAJAS" in texto.upper():
        return "RF625A"
    if "RF626A" in texto:
        return "RF626A"
    return por_defecto


def area_a_grupo(area_normalizada):
    """Igualdad exacta unicamente. Nunca contains/startswith/aproximado."""
    if not area_normalizada:
        return SIN_CLASIFICAR
    return AREA_A_GRUPO.get(area_normalizada, SIN_CLASIFICAR)


def resolver_grupo(areas_vistas):
    """
    areas_vistas: lista (en cualquier orden) de valores de Area ya
    normalizados y no vacios, observados para una misma etiqueta / un
    mismo archivo .db (una por pagina/particion en la que aparecieron).

    Con mas de una area distinta, el "mismo grupo, no es conflicto" solo
    aplica si TODAS las areas distintas son CONOCIDAS (estan en
    AREA_A_GRUPO) y ademas resuelven al mismo grupo canonico -p.ej.
    SEMIPALETS y SECO, ambas "seco"-. Si CUALQUIERA de las areas
    distintas es desconocida, o si las conocidas resuelven a grupos
    distintos, es CONFLICTO y el resultado es sin_clasificar: dos areas
    desconocidas distintas NO se consideran "del mismo grupo" solo
    porque ambas caigan en el fallback sin_clasificar -esa combinacion
    no se puede resolver con seguridad, podrian ser dos zonas nuevas y
    distintas que aun no se han dado de alta en el mapeo-.

    Formato determinista de grupo_fuente cuando hay mas de un valor de
    Area distinto: los valores distintos (ya en mayusculas) se ordenan
    ALFABETICAMENTE y se unen con "|". Se elige orden alfabetico -y no
    orden de aparicion- para que el mismo conjunto de areas produzca
    siempre el mismo texto en grupo_fuente, sin importar en que
    pagina/particion aparecio cada una primero.

    Devuelve (grupo, grupo_fuente):
      - sin areas
          -> (sin_clasificar, "NO_DETECTADO")
      - una unica area, conocida
          -> (su_grupo_canonico, "Area=<AREA>")
      - una unica area, desconocida
          -> (sin_clasificar, "Area=<AREA>")
      - >1 area distinta, TODAS conocidas y mismo grupo canonico
          -> (ese_grupo, "Area=AREA1|AREA2[...]")  (orden alfabetico)
      - >1 area distinta, alguna desconocida o grupos distintos
          -> (sin_clasificar, "CONFLICTO:AREA1|AREA2[...]")  (orden alfabetico)
    """
    distintas = []
    for area in areas_vistas:
        if area and area not in distintas:
            distintas.append(area)

    if not distintas:
        return SIN_CLASIFICAR, FUENTE_AUSENTE

    if len(distintas) == 1:
        area = distintas[0]
        return area_a_grupo(area), f"Area={area}"

    distintas_ordenadas = sorted(distintas)
    todas_conocidas = all(area in AREA_A_GRUPO for area in distintas)

    if todas_conocidas:
        grupos_resueltos = {AREA_A_GRUPO[area] for area in distintas}
        if len(grupos_resueltos) == 1:
            grupo_comun = next(iter(grupos_resueltos))
            return grupo_comun, "Area=" + "|".join(distintas_ordenadas)

    # O bien alguna area es desconocida, o las conocidas resuelven a mas
    # de un grupo canonico: no se puede decidir con seguridad.
    return SIN_CLASIFICAR, "CONFLICTO:" + "|".join(distintas_ordenadas)


def ensure_metadata_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS Metadata (
            Clave TEXT PRIMARY KEY NOT NULL,
            Valor TEXT NOT NULL
        )
        """
    )


def write_metadata(cur, grupo, grupo_fuente, formato_pdf):
    """
    Crea (si falta) y repuebla la tabla Metadata con las 4 claves fijas.
    Borra las filas previas antes de insertar para no dejar valores
    obsoletos al regenerar un archivo. `grupo` se valida siempre contra
    GRUPOS_VALIDOS: nunca queda vacio ni NULL.
    """
    if grupo not in GRUPOS_VALIDOS:
        grupo = SIN_CLASIFICAR

    ensure_metadata_table(cur)
    cur.execute("DELETE FROM Metadata")
    cur.executemany(
        "INSERT INTO Metadata (Clave, Valor) VALUES (?, ?)",
        [
            ("schema_version", SCHEMA_VERSION),
            ("grupo", grupo),
            ("grupo_fuente", grupo_fuente or FUENTE_AUSENTE),
            ("formato_pdf", formato_pdf or ""),
        ],
    )
