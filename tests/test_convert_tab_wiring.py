"""
Pruebas de ui/convert_tab.py con `st` sustituido por un doble (no hay
streamlit.testing.v1.AppTest como dependencia del proyecto): botón
deshabilitado mientras hay una operación en curso, bloqueo por tienda
independiente de session_state, restauración del estado tras errores o
interrupciones y conservación del resultado.
"""

import unittest
from unittest import mock

import ui.convert_tab as convert_tab

STORE = {"id": "tienda-1", "code": "14196", "name": "Tienda"}

# Estas pruebas usan objetos de subida opacos: la detección de AM613A
# (que lee el contenido del PDF) se cubre en test_convert_tab_am613a.py.
_sin_am613a = mock.patch.object(convert_tab, "am613a_de_subidas", return_value=[])


def setUpModule():
    _sin_am613a.start()


def tearDownModule():
    _sin_am613a.stop()


class SessionState(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def __setattr__(self, k, v):
        self[k] = v


class Rerun(BaseException):
    """Simula st.rerun(), que en Streamlit interrumpe el script."""


def _st_falso(en_curso=False, archivos=None):
    st = mock.MagicMock(name="st")
    st.session_state = SessionState(selected_store=STORE, flash_message="previo", last_summary=[])
    if en_curso:
        st.session_state[convert_tab.CONVERT_EN_CURSO_KEY] = True
    st.file_uploader.return_value = archivos
    st.rerun.side_effect = Rerun()
    return st


class BotonTests(unittest.TestCase):
    def test_sin_operacion_el_boton_esta_activo_y_usa_callback(self):
        st = _st_falso()
        with mock.patch.object(convert_tab, "st", st), \
             mock.patch.object(convert_tab, "convert_uploaded_files") as convertir:
            convert_tab.render_convert_tab()
        kwargs = st.button.call_args.kwargs
        self.assertFalse(kwargs["disabled"])
        self.assertIs(kwargs["on_click"], convert_tab._marcar_en_curso)
        convertir.assert_not_called()

    def test_callback_marca_la_operacion_en_curso(self):
        st = _st_falso()
        with mock.patch.object(convert_tab, "st", st):
            convert_tab._marcar_en_curso()
        self.assertTrue(st.session_state[convert_tab.CONVERT_EN_CURSO_KEY])

    def test_durante_la_operacion_boton_y_uploader_deshabilitados(self):
        st = _st_falso(en_curso=True, archivos=[object()])
        with mock.patch.object(convert_tab, "st", st), \
             mock.patch.object(convert_tab, "convert_uploaded_files", return_value=([], [])):
            with self.assertRaises(Rerun):
                convert_tab.render_convert_tab()
        self.assertTrue(st.button.call_args.kwargs["disabled"])
        self.assertTrue(st.file_uploader.call_args.kwargs["disabled"])


class OperacionTests(unittest.TestCase):
    def _ejecutar(self, st, **parches):
        with mock.patch.object(convert_tab, "st", st), mock.patch.multiple(convert_tab, **parches):
            with self.assertRaises(Rerun):
                convert_tab.render_convert_tab()

    def test_operacion_correcta_guarda_resultado_y_restaura_estado(self):
        st = _st_falso(en_curso=True, archivos=[object()])
        item = {"name": "a.db"}
        publicar = mock.Mock(return_value=[{"archivo": "a.db", "estado": "guardado", "detalle": "", "avisos": []}])
        self._ejecutar(
            st,
            convert_uploaded_files=mock.Mock(return_value=([item], [{"archivo": "a.pdf", "estado": "ok", "detalle": "a.db"}])),
            publicar_db_generados=publicar,
        )
        self.assertFalse(st.session_state[convert_tab.CONVERT_EN_CURSO_KEY])
        resultado = st.session_state[convert_tab.CONVERT_RESULTADO_KEY]
        self.assertEqual(resultado["nivel"], "success")
        self.assertEqual(resultado["conteo"]["guardados"], 1)
        self.assertEqual(st.session_state.flash_message, "")
        # store procede de la sesión del servidor.
        self.assertIs(publicar.call_args.args[0], STORE)
        self.assertFalse(convert_tab._bloqueo_de_tienda("tienda-1").locked())

    def test_error_en_conversion_restaura_estado_y_muestra_error(self):
        st = _st_falso(en_curso=True, archivos=[object()])
        self._ejecutar(st, convert_uploaded_files=mock.Mock(side_effect=RuntimeError("fitz roto")))
        self.assertFalse(st.session_state[convert_tab.CONVERT_EN_CURSO_KEY])
        resultado = st.session_state[convert_tab.CONVERT_RESULTADO_KEY]
        self.assertEqual(resultado["nivel"], "error")
        self.assertIn("fitz roto", resultado["mensaje"])
        self.assertFalse(convert_tab._bloqueo_de_tienda("tienda-1").locked())

    def test_error_en_publicacion_no_muestra_exito(self):
        st = _st_falso(en_curso=True, archivos=[object()])
        self._ejecutar(
            st,
            convert_uploaded_files=mock.Mock(return_value=([{"name": "a.db"}], [{"estado": "ok"}])),
            publicar_db_generados=mock.Mock(side_effect=RuntimeError("supabase")),
        )
        self.assertEqual(st.session_state[convert_tab.CONVERT_RESULTADO_KEY]["nivel"], "error")
        self.assertFalse(st.session_state[convert_tab.CONVERT_EN_CURSO_KEY])

    def test_interrupcion_de_streamlit_restaura_estado_y_libera_bloqueo(self):
        class Interrupcion(BaseException):
            pass

        st = _st_falso(en_curso=True, archivos=[object()])
        with mock.patch.object(convert_tab, "st", st), \
             mock.patch.object(convert_tab, "convert_uploaded_files", side_effect=Interrupcion()):
            with self.assertRaises(Interrupcion):
                convert_tab.render_convert_tab()
        self.assertFalse(st.session_state[convert_tab.CONVERT_EN_CURSO_KEY])
        self.assertFalse(convert_tab._bloqueo_de_tienda("tienda-1").locked())

    def test_sin_archivos_avisa_y_restaura(self):
        st = _st_falso(en_curso=True, archivos=[])
        convertir = mock.Mock()
        self._ejecutar(st, convert_uploaded_files=convertir)
        convertir.assert_not_called()
        self.assertEqual(st.session_state[convert_tab.CONVERT_RESULTADO_KEY]["nivel"], "warning")
        self.assertFalse(st.session_state[convert_tab.CONVERT_EN_CURSO_KEY])

    def test_bloqueo_de_tienda_ocupado_impide_otra_operacion(self):
        # Otra sesión (otro session_state) de la misma tienda en este
        # servidor ya tiene la operación en curso.
        bloqueo = convert_tab._bloqueo_de_tienda("tienda-1")
        bloqueo.acquire()
        try:
            st = _st_falso(en_curso=True, archivos=[object()])
            convertir = mock.Mock()
            self._ejecutar(st, convert_uploaded_files=convertir)
        finally:
            bloqueo.release()
        convertir.assert_not_called()
        resultado = st.session_state[convert_tab.CONVERT_RESULTADO_KEY]
        self.assertIn("en curso", resultado["mensaje"])
        self.assertFalse(st.session_state[convert_tab.CONVERT_EN_CURSO_KEY])

    def test_bloqueos_son_por_tienda(self):
        self.assertIsNot(convert_tab._bloqueo_de_tienda("tienda-1"), convert_tab._bloqueo_de_tienda("tienda-2"))
        self.assertIs(convert_tab._bloqueo_de_tienda("tienda-1"), convert_tab._bloqueo_de_tienda("tienda-1"))


class RenderResultadoTests(unittest.TestCase):
    def test_resultado_conservado_se_muestra_tras_el_rerun(self):
        st = _st_falso()
        st.session_state[convert_tab.CONVERT_RESULTADO_KEY] = {
            "nivel": "warning",
            "mensaje": "Guardados: 0, conflictos: 1.",
            "conversion": [{"archivo": "a.pdf", "estado": "ok", "detalle": "a.db"}],
            "publicacion": [{"archivo": "a.db", "estado": "conflicto", "detalle": "x", "avisos": ["aviso"]}],
        }
        with mock.patch.object(convert_tab, "st", st):
            convert_tab.render_convert_tab()
        self.assertEqual(st.warning.call_args_list, [
            mock.call("Guardados: 0, conflictos: 1."),
            mock.call("a.db: x"), mock.call("a.db: aviso"),
        ])
        st.success.assert_not_called()
        st.dataframe.assert_not_called()

    def test_resultado_normal_solo_muestra_resumen(self):
        st = _st_falso()
        resultado = {
            "nivel": "success", "mensaje": "2 archivos ya disponibles.",
            "conversion": [{"archivo": "a.pdf", "estado": "duplicado", "detalle": "repetido"}],
            "publicacion": [{"archivo": "a.db", "estado": "ya_disponible", "detalle": "disponible"}],
        }
        with mock.patch.object(convert_tab, "st", st):
            convert_tab._render_resultado(resultado)
        st.success.assert_called_once_with(resultado["mensaje"])
        st.warning.assert_not_called()
        st.error.assert_not_called()
        st.dataframe.assert_not_called()

    def test_error_por_archivo_sigue_visible_sin_tabla(self):
        st = _st_falso()
        with mock.patch.object(convert_tab, "st", st):
            convert_tab._render_resultado({
                "nivel": "warning", "mensaje": "Hubo un error.",
                "conversion": [{"archivo": "a.pdf", "estado": "error", "detalle": "PDF inválido"}],
            })
        st.error.assert_called_once_with("a.pdf: PDF inválido")
        st.dataframe.assert_not_called()

    def test_nivel_error_usa_st_error(self):
        st = _st_falso()
        st.session_state[convert_tab.CONVERT_RESULTADO_KEY] = {"nivel": "error", "mensaje": "m"}
        with mock.patch.object(convert_tab, "st", st):
            convert_tab.render_convert_tab()
        st.error.assert_called_once_with("m")
        st.success.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
