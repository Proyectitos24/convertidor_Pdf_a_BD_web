"""
Helpers puros (sin Streamlit, sin red) para buscar, filtrar, agrupar y
decidir qué grupo debe quedar abierto en el acordeón controlado de
"Archivos 24h". Separados de la interfaz para poder probarlos sin
mockear nada.

Este módulo ya NO contiene lógica de selección múltiple ni de descarga
por lotes: la pestaña "Archivos 24h" se simplificó a descarga y
eliminación individuales únicamente (ver ui/files_tab.py).
"""

from services.grupo_packinglist import GRUPO_ETIQUETAS, GRUPO_ORDEN, SIN_CLASIFICAR

FILTRO_TODOS = "todos"


def filtrar_archivos(archivos, texto_busqueda=None, grupo_filtro=None):
    """
    Busca por db_file_name u original_pdf_name (sin distinguir mayúsculas),
    y filtra por grupo si se indica uno distinto de FILTRO_TODOS/None.
    No modifica la lista recibida.
    """
    texto = (texto_busqueda or "").strip().lower()
    resultado = []

    for archivo in archivos:
        if grupo_filtro and grupo_filtro != FILTRO_TODOS:
            if (archivo.get("grupo") or SIN_CLASIFICAR) != grupo_filtro:
                continue

        if texto:
            nombre = (archivo.get("db_file_name") or "").lower()
            origen = (archivo.get("original_pdf_name") or "").lower()
            if texto not in nombre and texto not in origen:
                continue

        resultado.append(archivo)

    return resultado


def agrupar_por_grupo(archivos):
    """
    Devuelve una lista de (grupo, etiqueta_visible, [archivos]) siguiendo
    GRUPO_ORDEN, omitiendo los grupos sin archivos visibles (tras aplicar
    filtrar_archivos). No inventa un grupo nuevo si aparece un valor no
    reconocido: eso ya lo evita el resto del sistema (Metadata siempre
    guarda un grupo de GRUPOS_VALIDOS).

    Si `archivos` ya viene filtrado por un grupo concreto (filtrar_archivos
    con grupo_filtro distinto de FILTRO_TODOS), el resultado contiene
    exclusivamente ese grupo — no hace falta ningún caso especial aquí.
    """
    por_grupo = {}
    for archivo in archivos:
        grupo = archivo.get("grupo") or SIN_CLASIFICAR
        por_grupo.setdefault(grupo, []).append(archivo)

    secciones = []
    for grupo in GRUPO_ORDEN:
        if grupo in por_grupo:
            secciones.append((grupo, GRUPO_ETIQUETAS[grupo], por_grupo[grupo]))
    return secciones


def grupo_abierto_tras_cambios(
    grupo_abierto_actual, grupo_filtro_actual, grupo_filtro_anterior, secciones_visibles
):
    """
    Decide qué grupo debe quedar abierto en el acordeón controlado tras un
    rerun, SIN gestionar el toggle de clic en un encabezado (eso lo hace
    directamente ui/files_tab.py, mediante el callback on_click del botón
    de encabezado y el rerun normal que Streamlit ya dispara tras el
    clic — sin ningún st.rerun() explícito).

    - Si el filtro de grupo acaba de cambiar a un grupo concreto (no
      FILTRO_TODOS), se autoabre ese grupo si tiene resultados, o se
      cierra todo si no los tiene.
    - Si el filtro acaba de cambiar a FILTRO_TODOS, se cierra todo.
    - Si el filtro no ha cambiado, se conserva el grupo abierto actual
      siempre que siga entre las secciones visibles (con resultados);
      si ya no está (porque la búsqueda lo dejó sin coincidencias o se
      borró su último archivo), se cierra.
    """
    grupos_disponibles = {grupo for grupo, _etiqueta, _archivos in secciones_visibles}

    if grupo_filtro_actual != grupo_filtro_anterior:
        if grupo_filtro_actual != FILTRO_TODOS:
            return grupo_filtro_actual if grupo_filtro_actual in grupos_disponibles else None
        return None

    if grupo_abierto_actual not in grupos_disponibles:
        return None

    return grupo_abierto_actual
