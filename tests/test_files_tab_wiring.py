"""
Comprueba la pestaña "Archivos 24h" (ui/files_tab.py). La mayoría de las
propiedades estructurales (sin selector obligatorio, acordeón
controlado por session_state, indicador Pendiente/Descargado,
descarga/eliminación siempre individuales) se confirman por inspección
del código fuente, igual que el resto de ui/*.py (no hay
streamlit.testing.v1.AppTest como dependencia del proyecto).

La caché de descarga (_descargar_db_bytes_cacheado, st.cache_data) y el
callback del encabezado (_alternar_grupo_abierto) sí se prueban
llamándolos de verdad: st.session_state y st.cache_data funcionan fuera
de una app Streamlit real (con avisos por consola, sin
ScriptRunContext), así que aquí se aprovecha para verificar
comportamiento real de caché/memoria en vez de solo texto.
"""

import unittest
from pathlib import Path
from unittest import mock

import streamlit as st

import ui.files_tab as files_tab

FILES_TAB_PATH = Path(files_tab.__file__)


class BuscadorYFiltroSiempreVisiblesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fuente = FILES_TAB_PATH.read_text(encoding="utf-8")

    def test_existe_todos_los_grupos_como_opcion_inicial(self):
        self.assertIn("Todos los grupos", self.fuente)
        self.assertIn("FILTRO_TODOS", self.fuente)

    def test_opciones_del_filtro_vienen_de_grupo_orden_completo(self):
        # Las opciones del selector de filtro deben ser la lista canónica
        # completa (FILTRO_TODOS + GRUPO_ORDEN), no solo los grupos que
        # ya tienen archivos: así seleccionar un grupo vacío sigue siendo
        # posible y muestra el mensaje de "sin coincidencias".
        self.assertIn("OPCIONES_FILTRO = [FILTRO_TODOS] + GRUPO_ORDEN", self.fuente)

    def test_no_hay_selector_obligatorio_que_bloquee_la_vista(self):
        self.assertNotIn("Selecciona un grupo", self.fuente)
        self.assertNotIn("placeholder=\"Selecciona un grupo\"", self.fuente)

    def test_buscador_y_filtro_se_crean_antes_de_cualquier_encabezado(self):
        indice_busqueda = self.fuente.index("st.text_input(")
        indice_filtro = self.fuente.index("st.selectbox(")
        indice_header = self.fuente.index('key=f"files_tab_header_')
        self.assertLess(indice_busqueda, indice_header)
        self.assertLess(indice_filtro, indice_header)

    def test_no_hay_return_temprano_antes_de_mostrar_encabezados(self):
        # A diferencia del diseño anterior, no debe existir ningún
        # "return" condicionado a que no haya un grupo elegido en un
        # selector obligatorio.
        self.assertNotIn("if grupo_seleccionado is None:", self.fuente)


class AcordeonControladoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fuente = FILES_TAB_PATH.read_text(encoding="utf-8")

    def test_no_usa_st_expander(self):
        self.assertNotIn("st.expander(", self.fuente)

    def test_usa_session_state_grupo_abierto(self):
        self.assertIn('GRUPO_ABIERTO_KEY = "files_tab_grupo_abierto"', self.fuente)

    def test_usa_la_funcion_pura_de_decision(self):
        self.assertIn("from services.file_selection import", self.fuente)
        self.assertIn("grupo_abierto_tras_cambios", self.fuente)

    def test_encabezado_es_un_boton_de_ancho_completo(self):
        self.assertIn("st.button(", self.fuente)
        self.assertIn('key=f"files_tab_header_{grupo}"', self.fuente)
        self.assertIn("use_container_width=True", self.fuente)

    def test_flecha_cerrado_y_abierto(self):
        self.assertIn('"▼" if abierto else "▶"', self.fuente)

    def test_encabezado_no_se_evalua_con_if_st_button(self):
        # El botón del encabezado ya no se evalúa con "if st.button(...)":
        # el cambio de estado ocurre en el callback on_click, no tras
        # comprobar el valor de retorno del clic. (El "if st.button("
        # del botón de eliminar 🗑️ es otro botón, no el del encabezado.)
        indice_header = self.fuente.index('key=f"files_tab_header_{grupo}"')
        inicio_statement = self.fuente.rindex("\n", 0, indice_header - 200)
        statement = self.fuente[inicio_statement:indice_header]
        self.assertNotIn("if st.button(", statement)
        bloque = self.fuente[max(0, indice_header - 400) : indice_header + 200]
        self.assertIn("on_click=_alternar_grupo_abierto", bloque)
        self.assertIn("args=(grupo,)", bloque)

    def test_callback_alterna_cerrando_el_mismo_o_abriendo_otro(self):
        self.assertIn(
            'st.session_state[GRUPO_ABIERTO_KEY] = None if abierto_actual == grupo else grupo',
            self.fuente,
        )

    def test_no_hay_un_segundo_rerun_para_abrir_o_cerrar_encabezados(self):
        # Los únicos st.rerun() como SENTENCIA de código deben ser los
        # del diálogo de confirmación de borrado (confirmar/cancelar):
        # ninguno asociado a abrir/cerrar un grupo del acordeón. (El
        # docstring del módulo menciona "st.rerun()" dos veces en
        # prosa, por eso se cuenta por línea de código, no substring.)
        import re

        llamadas_como_codigo = re.findall(r"^\s*st\.rerun\(\)\s*$", self.fuente, re.MULTILINE)
        self.assertEqual(len(llamadas_como_codigo), 2)

        indice_dialogo = self.fuente.index("def _dialog_confirmar_eliminacion(")
        indice_render = self.fuente.index("def render_files_tab(")
        primera = re.search(r"^\s*st\.rerun\(\)\s*$", self.fuente, re.MULTILINE)
        self.assertGreater(primera.start(), indice_dialogo)
        self.assertLess(primera.start(), indice_render)

    def test_cards_solo_se_pintan_dentro_del_if_abierto(self):
        indice_if_abierto = self.fuente.index("if abierto:")
        indice_render_fila = self.fuente.index("_render_fila_archivo(store_id, archivo)")
        self.assertLess(indice_if_abierto, indice_render_fila)


class BusquedaYFiltroCombinadosTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fuente = FILES_TAB_PATH.read_text(encoding="utf-8")

    def test_filtrar_archivos_recibe_texto_y_grupo(self):
        self.assertIn("filtrar_archivos(archivos, texto_busqueda, grupo_filtro)", self.fuente)

    def test_secciones_se_construyen_a_partir_de_los_visibles(self):
        self.assertIn("secciones = agrupar_por_grupo(visibles)", self.fuente)

    def test_filtrado_ocurre_antes_de_agrupar(self):
        indice_filtrar = self.fuente.index("filtrar_archivos(")
        indice_agrupar = self.fuente.index("agrupar_por_grupo(visibles)")
        self.assertLess(indice_filtrar, indice_agrupar)

    def test_mensaje_si_no_hay_secciones(self):
        self.assertIn("if not secciones:", self.fuente)
        self.assertIn("Ningún archivo coincide con la búsqueda/filtro actual.", self.fuente)


class ContadorPequenoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fuente = FILES_TAB_PATH.read_text(encoding="utf-8")

    def test_no_usa_st_metric(self):
        self.assertNotIn("st.metric", self.fuente)

    def test_texto_del_contador(self):
        self.assertIn('f"Mostrando {len(visibles)} de {len(archivos)} archivos"', self.fuente)


class SinSeleccionNiLotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fuente = FILES_TAB_PATH.read_text(encoding="utf-8")

    def test_no_hay_checkbox(self):
        self.assertNotIn("st.checkbox", self.fuente)

    def test_no_hay_estado_de_seleccion(self):
        for termino in ("SELECCION_KEY", "seleccionados_ids", "checkbox_key"):
            with self.subTest(termino=termino):
                self.assertNotIn(termino, self.fuente)

    def test_no_hay_textos_ni_botones_de_seleccion_por_lote(self):
        for texto in (
            "Seleccionar grupo",
            "Deseleccionar grupo",
            "Limpiar selección",
            "Descargar seleccionados",
            "Eliminar seleccionados",
            "Descargar grupo",
        ):
            with self.subTest(texto=texto):
                self.assertNotIn(texto, self.fuente)

    def test_no_hay_descarga_por_lote_ni_componente_js(self):
        for termino in (
            "PAYLOAD_KEY",
            "render_multi_download_component",
            "download_component",
            "components.html",
            "components.v1",
            "cabe_en_un_lote",
            "MAX_DESCARGA_LOTE",
            "construir_payload_descarga",
        ):
            with self.subTest(termino=termino):
                self.assertNotIn(termino, self.fuente)

    def test_no_hay_preparar_ni_regenerar_enlace(self):
        for texto in ("Preparar descarga", "Regenerar enlace", "Descargar ahora"):
            with self.subTest(texto=texto):
                self.assertNotIn(texto, self.fuente)

    def test_no_usa_urls_prefirmadas(self):
        for termino in ("import generate_download_url", "from services.presigned_url_cache import", "entrada_valida(", "construir_entrada("):
            with self.subTest(termino=termino):
                self.assertNotIn(termino, self.fuente)


class DescargaIndividualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fuente = FILES_TAB_PATH.read_text(encoding="utf-8")

    def test_usa_el_envoltorio_cacheado_no_download_db_bytes_directo(self):
        self.assertIn('_descargar_db_bytes_cacheado(archivo["object_key"])', self.fuente)
        # download_db_bytes en sí solo debe llamarse UNA vez en todo el
        # archivo: dentro del propio envoltorio cacheado.
        self.assertEqual(self.fuente.count("download_db_bytes("), 1)
        self.assertIn("return download_db_bytes(object_key)", self.fuente)

    def test_llamada_cacheada_ocurre_solo_dentro_de_render_fila_archivo(self):
        indice_def = self.fuente.index("def _render_fila_archivo(")
        indice_llamada = self.fuente.index('_descargar_db_bytes_cacheado(archivo["object_key"])')
        self.assertGreater(indice_llamada, indice_def)

    def test_download_button_marca_downloaded_at_con_mark_file_downloaded(self):
        self.assertIn("on_click=mark_file_downloaded", self.fuente)
        self.assertIn('args=(archivo["id"],)', self.fuente)

    def test_boton_se_llama_siempre_descargar_sin_condicional(self):
        self.assertIn('label="Descargar"', self.fuente)
        self.assertNotIn("Volver a descargar", self.fuente)

    def test_cada_boton_de_descarga_tiene_clave_unica_por_archivo(self):
        self.assertIn('key=f"download_{archivo[\'id\']}"', self.fuente)

    def test_indicador_pendiente_o_descargado_con_emoji(self):
        self.assertIn('"✅ Descargado" if downloaded else "🕓 Pendiente"', self.fuente)
        self.assertIn('archivo.get("downloaded_at") is not None', self.fuente)

    def test_boton_eliminar_usa_icono_papelera(self):
        self.assertIn('"🗑️"', self.fuente)

    def test_cada_boton_de_eliminar_tiene_clave_unica_por_archivo(self):
        self.assertIn('key=f"del_{archivo[\'id\']}"', self.fuente)


class EliminacionIndividualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fuente = FILES_TAB_PATH.read_text(encoding="utf-8")

    def test_eliminar_archivos_seguro_recibe_una_lista_de_un_solo_id(self):
        self.assertRegex(
            self.fuente,
            r'eliminar_archivos_seguro\(\s*store_id,\s*\[archivo\["id"\]\],',
        )

    def test_dialogo_de_confirmacion_muestra_el_nombre_del_archivo(self):
        self.assertIn("Confirmar eliminación", self.fuente)
        self.assertIn("archivo['db_file_name']", self.fuente)

    def test_conserva_eliminar_archivos_seguro(self):
        self.assertIn("from services.file_deletion import eliminar_archivos_seguro", self.fuente)

    def test_borrar_limpia_la_entrada_de_cache_de_ese_archivo(self):
        indice_dialogo = self.fuente.index("def _dialog_confirmar_eliminacion(")
        indice_clear = self.fuente.index(
            '_descargar_db_bytes_cacheado.clear(archivo["object_key"])'
        )
        self.assertGreater(indice_clear, indice_dialogo)


class ErrorDeDescargaAisladoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fuente = FILES_TAB_PATH.read_text(encoding="utf-8")

    def test_la_descarga_esta_envuelta_en_try_except(self):
        seccion = self.fuente[self.fuente.index("def _render_fila_archivo(") :]
        indice_try = seccion.index("try:")
        indice_llamada = seccion.index("_descargar_db_bytes_cacheado(")
        indice_except = seccion.index("except Exception:")
        indice_error = seccion.index("st.error(")
        self.assertLess(indice_try, indice_llamada)
        self.assertLess(indice_llamada, indice_except)
        self.assertLess(indice_except, indice_error)

    def test_el_download_button_solo_se_crea_si_no_hubo_error(self):
        seccion = self.fuente[self.fuente.index("def _render_fila_archivo(") :]
        indice_else = seccion.index("else:")
        indice_download_button = seccion.index("st.download_button(")
        self.assertLess(indice_else, indice_download_button)


class AlternarGrupoAbiertoTests(unittest.TestCase):
    """
    Prueba real (no solo de texto) del callback on_click del
    encabezado: st.session_state funciona fuera de una app Streamlit
    real (con avisos, sin ScriptRunContext), así que se puede invocar
    directamente.
    """

    def test_abre_un_grupo_cerrado(self):
        st.session_state[files_tab.GRUPO_ABIERTO_KEY] = None
        files_tab._alternar_grupo_abierto("seco")
        self.assertEqual(st.session_state[files_tab.GRUPO_ABIERTO_KEY], "seco")

    def test_pulsar_el_grupo_ya_abierto_lo_cierra(self):
        st.session_state[files_tab.GRUPO_ABIERTO_KEY] = "seco"
        files_tab._alternar_grupo_abierto("seco")
        self.assertIsNone(st.session_state[files_tab.GRUPO_ABIERTO_KEY])

    def test_abrir_otro_grupo_cierra_el_anterior(self):
        st.session_state[files_tab.GRUPO_ABIERTO_KEY] = "seco"
        files_tab._alternar_grupo_abierto("refrigerado")
        self.assertEqual(st.session_state[files_tab.GRUPO_ABIERTO_KEY], "refrigerado")


class DescargarDbBytesCacheadoTests(unittest.TestCase):
    """
    Prueba real de _descargar_db_bytes_cacheado (st.cache_data): cubre
    que repetir el mismo object_key no vuelve a llamar a
    download_db_bytes, que claves distintas sí generan llamadas
    distintas, que un fallo NO se cachea (el siguiente intento
    reintenta de verdad), y que .clear(object_key) solo afecta a esa
    clave.
    """

    def setUp(self):
        files_tab._descargar_db_bytes_cacheado.clear()

    def tearDown(self):
        files_tab._descargar_db_bytes_cacheado.clear()

    def test_repetir_la_misma_clave_no_vuelve_a_descargar(self):
        llamada = mock.Mock(return_value=b"contenido")
        with mock.patch.object(files_tab, "download_db_bytes", llamada):
            resultado1 = files_tab._descargar_db_bytes_cacheado("stores/x/a.db")
            resultado2 = files_tab._descargar_db_bytes_cacheado("stores/x/a.db")

        self.assertEqual(resultado1, b"contenido")
        self.assertEqual(resultado2, b"contenido")
        llamada.assert_called_once_with("stores/x/a.db")

    def test_claves_distintas_provocan_llamadas_distintas(self):
        llamada = mock.Mock(side_effect=lambda clave: clave.encode())
        with mock.patch.object(files_tab, "download_db_bytes", llamada):
            files_tab._descargar_db_bytes_cacheado("stores/x/a.db")
            files_tab._descargar_db_bytes_cacheado("stores/x/b.db")

        self.assertEqual(llamada.call_count, 2)

    def test_un_fallo_no_se_cachea_el_siguiente_intento_reintenta(self):
        llamada = mock.Mock(side_effect=[RuntimeError("r2 caído"), b"ok"])
        with mock.patch.object(files_tab, "download_db_bytes", llamada):
            with self.assertRaises(RuntimeError):
                files_tab._descargar_db_bytes_cacheado("stores/x/c.db")
            resultado = files_tab._descargar_db_bytes_cacheado("stores/x/c.db")

        self.assertEqual(resultado, b"ok")
        self.assertEqual(llamada.call_count, 2)

    def test_clear_de_una_clave_no_afecta_a_las_demas(self):
        llamada = mock.Mock(side_effect=lambda clave: clave.encode())
        with mock.patch.object(files_tab, "download_db_bytes", llamada):
            files_tab._descargar_db_bytes_cacheado("stores/x/a.db")
            files_tab._descargar_db_bytes_cacheado("stores/x/b.db")
            files_tab._descargar_db_bytes_cacheado.clear("stores/x/a.db")
            files_tab._descargar_db_bytes_cacheado("stores/x/a.db")
            files_tab._descargar_db_bytes_cacheado("stores/x/b.db")

        # a.db se volvió a pedir (3ª llamada), b.db no se repitió.
        self.assertEqual(llamada.call_count, 3)

    def test_la_clave_efectiva_depende_solo_del_object_key(self):
        llamada = mock.Mock(return_value=b"contenido")
        with mock.patch.object(files_tab, "download_db_bytes", llamada):
            files_tab._descargar_db_bytes_cacheado("stores/x/mismo.db")
            files_tab._descargar_db_bytes_cacheado("stores/x/mismo.db")
            files_tab._descargar_db_bytes_cacheado("stores/x/mismo.db")

        llamada.assert_called_once_with("stores/x/mismo.db")


if __name__ == "__main__":
    unittest.main(verbosity=2)
