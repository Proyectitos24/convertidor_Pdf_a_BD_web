"""
Pruebas de services.file_selection: búsqueda, filtro por grupo,
agrupado y decisión de qué grupo queda abierto en el acordeón
controlado de "Archivos 24h". Todo puro, sin Streamlit ni red.

Ya no cubre selección múltiple ni descarga por lotes: esa lógica se
retiró de "Archivos 24h" (pestaña simplificada a descarga/eliminación
individuales).
"""

import unittest

from services.file_selection import (
    FILTRO_TODOS,
    agrupar_por_grupo,
    filtrar_archivos,
    grupo_abierto_tras_cambios,
)

ARCHIVOS = [
    {"id": "1", "db_file_name": "packinglist_a.db", "original_pdf_name": "RF626A_a.pdf", "grupo": "refrigerado"},
    {"id": "2", "db_file_name": "packinglist_b.db", "original_pdf_name": "RF626A_b.pdf", "grupo": "refrigerado"},
    {"id": "3", "db_file_name": "packinglist_c.db", "original_pdf_name": "RF625A_c.pdf", "grupo": "almacen_central"},
    {"id": "4", "db_file_name": "packinglist_d.db", "original_pdf_name": "RF626A_d.pdf", "grupo": "sin_clasificar"},
]


class FiltrarArchivosTests(unittest.TestCase):
    def test_sin_filtros_devuelve_todo(self):
        self.assertEqual(filtrar_archivos(ARCHIVOS), ARCHIVOS)

    def test_busqueda_por_nombre_db(self):
        resultado = filtrar_archivos(ARCHIVOS, texto_busqueda="packinglist_a")
        self.assertEqual([a["id"] for a in resultado], ["1"])

    def test_busqueda_por_pdf_origen(self):
        resultado = filtrar_archivos(ARCHIVOS, texto_busqueda="RF625A")
        self.assertEqual([a["id"] for a in resultado], ["3"])

    def test_busqueda_no_distingue_mayusculas(self):
        resultado = filtrar_archivos(ARCHIVOS, texto_busqueda="rf626a_a")
        self.assertEqual([a["id"] for a in resultado], ["1"])

    def test_filtro_por_grupo(self):
        resultado = filtrar_archivos(ARCHIVOS, grupo_filtro="refrigerado")
        self.assertEqual({a["id"] for a in resultado}, {"1", "2"})

    def test_filtro_todos_no_filtra(self):
        resultado = filtrar_archivos(ARCHIVOS, grupo_filtro=FILTRO_TODOS)
        self.assertEqual(len(resultado), len(ARCHIVOS))

    def test_busqueda_y_filtro_combinados(self):
        resultado = filtrar_archivos(ARCHIVOS, texto_busqueda="packinglist_b", grupo_filtro="refrigerado")
        self.assertEqual([a["id"] for a in resultado], ["2"])

    def test_sin_coincidencias(self):
        self.assertEqual(filtrar_archivos(ARCHIVOS, texto_busqueda="no-existe"), [])

    def test_no_modifica_la_lista_original(self):
        original = list(ARCHIVOS)
        filtrar_archivos(ARCHIVOS, texto_busqueda="packinglist_a")
        self.assertEqual(ARCHIVOS, original)


class AgruparPorGrupoTests(unittest.TestCase):
    def test_respeta_el_orden_canonico(self):
        secciones = agrupar_por_grupo(ARCHIVOS)
        grupos_presentes = [grupo for grupo, _etq, _archivos in secciones]
        # almacen_central va antes que refrigerado y sin_clasificar en GRUPO_ORDEN
        self.assertEqual(grupos_presentes, ["almacen_central", "refrigerado", "sin_clasificar"])

    def test_omite_grupos_sin_archivos(self):
        secciones = agrupar_por_grupo(ARCHIVOS)
        grupos_presentes = {grupo for grupo, _etq, _archivos in secciones}
        self.assertNotIn("congelado", grupos_presentes)
        self.assertNotIn("pollo_carne", grupos_presentes)

    def test_etiqueta_visible_correcta(self):
        secciones = dict((grupo, etq) for grupo, etq, _archivos in agrupar_por_grupo(ARCHIVOS))
        self.assertEqual(secciones["almacen_central"], "Almacén Central")
        self.assertEqual(secciones["sin_clasificar"], "Sin clasificar")

    def test_cuenta_correcta_por_grupo(self):
        secciones = {grupo: len(archivos) for grupo, _etq, archivos in agrupar_por_grupo(ARCHIVOS)}
        self.assertEqual(secciones["refrigerado"], 2)
        self.assertEqual(secciones["almacen_central"], 1)

    def test_ningun_grupo_vacio_aparece_en_el_resultado(self):
        for _grupo, _etq, archivos_grupo in agrupar_por_grupo(ARCHIVOS):
            self.assertGreater(len(archivos_grupo), 0)


class FiltroDeGrupoConcretoTests(unittest.TestCase):
    """
    Reproduce exactamente lo que hace render_files_tab: primero
    filtrar_archivos(archivos, texto, grupo_filtro) y luego
    agrupar_por_grupo(visibles). Al elegir un grupo concreto en el
    selector, el resultado debe contener EXCLUSIVAMENTE ese acordeón.
    """

    def test_filtrar_por_almacen_central_y_agrupar_da_solo_ese_grupo(self):
        visibles = filtrar_archivos(ARCHIVOS, grupo_filtro="almacen_central")
        secciones = agrupar_por_grupo(visibles)
        self.assertEqual([grupo for grupo, _etq, _archivos in secciones], ["almacen_central"])

    def test_comportamiento_igual_para_cualquier_grupo(self):
        for grupo_elegido in ("refrigerado", "sin_clasificar"):
            with self.subTest(grupo=grupo_elegido):
                visibles = filtrar_archivos(ARCHIVOS, grupo_filtro=grupo_elegido)
                secciones = agrupar_por_grupo(visibles)
                self.assertEqual([g for g, _e, _a in secciones], [grupo_elegido])

    def test_grupo_sin_archivos_no_genera_acordeon_vacio(self):
        visibles = filtrar_archivos(ARCHIVOS, grupo_filtro="congelado")
        secciones = agrupar_por_grupo(visibles)
        self.assertEqual(secciones, [])


class GrupoAbiertoTrasCambiosTests(unittest.TestCase):
    """
    Requisito 1: nada abierto al entrar con "Todos los grupos" (llamador
    pasa grupo_abierto_actual=None, grupo_filtro_actual=FILTRO_TODOS,
    grupo_filtro_anterior=None la primera vez).
    """

    def setUp(self):
        self.secciones_completas = agrupar_por_grupo(ARCHIVOS)

    def test_nada_abierto_al_entrar(self):
        resultado = grupo_abierto_tras_cambios(
            grupo_abierto_actual=None,
            grupo_filtro_actual=FILTRO_TODOS,
            grupo_filtro_anterior=None,
            secciones_visibles=self.secciones_completas,
        )
        self.assertIsNone(resultado)

    def test_elegir_un_grupo_concreto_en_el_filtro_lo_autoabre(self):
        visibles = filtrar_archivos(ARCHIVOS, grupo_filtro="refrigerado")
        secciones = agrupar_por_grupo(visibles)
        resultado = grupo_abierto_tras_cambios(
            grupo_abierto_actual=None,
            grupo_filtro_actual="refrigerado",
            grupo_filtro_anterior=FILTRO_TODOS,
            secciones_visibles=secciones,
        )
        self.assertEqual(resultado, "refrigerado")

    def test_elegir_un_grupo_sin_archivos_no_abre_nada(self):
        visibles = filtrar_archivos(ARCHIVOS, grupo_filtro="congelado")
        secciones = agrupar_por_grupo(visibles)
        resultado = grupo_abierto_tras_cambios(
            grupo_abierto_actual=None,
            grupo_filtro_actual="congelado",
            grupo_filtro_anterior=FILTRO_TODOS,
            secciones_visibles=secciones,
        )
        self.assertIsNone(resultado)

    def test_volver_a_todos_los_grupos_cierra_todo(self):
        resultado = grupo_abierto_tras_cambios(
            grupo_abierto_actual="refrigerado",
            grupo_filtro_actual=FILTRO_TODOS,
            grupo_filtro_anterior="refrigerado",
            secciones_visibles=self.secciones_completas,
        )
        self.assertIsNone(resultado)

    def test_filtro_sin_cambios_conserva_el_grupo_abierto(self):
        resultado = grupo_abierto_tras_cambios(
            grupo_abierto_actual="refrigerado",
            grupo_filtro_actual=FILTRO_TODOS,
            grupo_filtro_anterior=FILTRO_TODOS,
            secciones_visibles=self.secciones_completas,
        )
        self.assertEqual(resultado, "refrigerado")

    def test_busqueda_que_deja_sin_resultados_al_grupo_abierto_lo_cierra(self):
        # Simula: el grupo "refrigerado" estaba abierto, y una búsqueda
        # nueva no coincide con ninguno de sus archivos (el filtro de
        # grupo no cambió, solo el texto de búsqueda).
        visibles = filtrar_archivos(ARCHIVOS, texto_busqueda="no-existe")
        secciones = agrupar_por_grupo(visibles)
        resultado = grupo_abierto_tras_cambios(
            grupo_abierto_actual="refrigerado",
            grupo_filtro_actual=FILTRO_TODOS,
            grupo_filtro_anterior=FILTRO_TODOS,
            secciones_visibles=secciones,
        )
        self.assertIsNone(resultado)

    def test_borrar_el_ultimo_archivo_del_grupo_abierto_lo_cierra(self):
        # Simula: "almacen_central" tenía un solo archivo (id "3") y se
        # elimina, así que ya no aparece entre las secciones visibles.
        archivos_sin_el_borrado = [a for a in ARCHIVOS if a["id"] != "3"]
        secciones = agrupar_por_grupo(archivos_sin_el_borrado)
        resultado = grupo_abierto_tras_cambios(
            grupo_abierto_actual="almacen_central",
            grupo_filtro_actual=FILTRO_TODOS,
            grupo_filtro_anterior=FILTRO_TODOS,
            secciones_visibles=secciones,
        )
        self.assertIsNone(resultado)

    def test_descargar_un_archivo_no_cierra_el_grupo(self):
        # Descargar no quita el archivo de la lista ni cambia el filtro:
        # el grupo abierto debe seguir siéndolo.
        resultado = grupo_abierto_tras_cambios(
            grupo_abierto_actual="refrigerado",
            grupo_filtro_actual=FILTRO_TODOS,
            grupo_filtro_anterior=FILTRO_TODOS,
            secciones_visibles=self.secciones_completas,
        )
        self.assertEqual(resultado, "refrigerado")

    def test_cancelar_una_eliminacion_no_cierra_el_grupo(self):
        # Cancelar no cambia nada de la lista ni del filtro: mismo caso
        # que un rerun sin cambios reales.
        resultado = grupo_abierto_tras_cambios(
            grupo_abierto_actual="refrigerado",
            grupo_filtro_actual=FILTRO_TODOS,
            grupo_filtro_anterior=FILTRO_TODOS,
            secciones_visibles=self.secciones_completas,
        )
        self.assertEqual(resultado, "refrigerado")

    def test_cambiar_de_un_grupo_concreto_a_otro_grupo_concreto(self):
        visibles = filtrar_archivos(ARCHIVOS, grupo_filtro="almacen_central")
        secciones = agrupar_por_grupo(visibles)
        resultado = grupo_abierto_tras_cambios(
            grupo_abierto_actual="refrigerado",
            grupo_filtro_actual="almacen_central",
            grupo_filtro_anterior="refrigerado",
            secciones_visibles=secciones,
        )
        self.assertEqual(resultado, "almacen_central")


if __name__ == "__main__":
    unittest.main(verbosity=2)
